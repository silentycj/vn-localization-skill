"""Google AI Studio per-key & per-project quota allocation and lifecycle manager.

Tracks RPM, TPM (including thinking tokens), and daily RPD per key / shared GCP
project pool using persistent JSON state with automatic 00:00 Pacific Time reset.
Never stores raw secret API keys in persistent state files.

Usage:
    python key_quota_manager.py status [--keys-file <api_keys.txt>] [--project <gt_project>] [--model gemini-4-hydrogen]
    python key_quota_manager.py set-limit --keys-file <api_keys.txt> --key all --model gemini-4-hydrogen --rpm 5 --tpm 250000 --rpd 20 [--reserve 0] [--cap 20] [--project-group projA] [--label main]
    python key_quota_manager.py plan [--keys-file <api_keys.txt>] [--project <gt_project>] [--requests 40] [--model gemini-4-hydrogen]
    python key_quota_manager.py check-models [--keys-file <api_keys.txt>] [--model gemini-4-hydrogen]
    python key_quota_manager.py reset [--keys-file <api_keys.txt>] --key all [--daily] [--cooldown]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo

    PT_TZ = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover - fallback only if tzdata unavailable
    PT_TZ = timezone(timedelta(hours=-8))


# Official/observed Free Tier reference defaults per model (enforced per GCP project).
# Note: Google adjusts free-tier quotas dynamically; verify per-project values at
# https://aistudio.google.com/rate-limit and override via `set-limit` when needed.
MODEL_FREE_LIMITS: dict[str, dict[str, Any]] = {
    "gemini-4-hydrogen": {
        "rpm": 5,
        "tpm": 250_000,
        "rpd": 20,
        "notes": "Free tier (5 RPM / 250K input TPM / 20 RPD per project; thinkingLevel=HIGH tokens count toward token budget)",
    },
    "gemini-2.5-flash": {
        "rpm": 5,
        "tpm": 250_000,
        "rpd": 20,
        "notes": "Free tier per project (dynamic 20+ RPD)",
    },
    "gemini-2.5-pro": {
        "rpm": 5,
        "tpm": 250_000,
        "rpd": 100,
        "notes": "Free tier per project",
    },
    "gemini-2.5-flash-lite": {
        "rpm": 15,
        "tpm": 250_000,
        "rpd": 1000,
        "notes": "Free tier per project (varies 20-1000 RPD dynamically)",
    },
    "gemini-3.1-flash-lite": {
        "rpm": 15,
        "tpm": 250_000,
        "rpd": 500,
        "notes": "Free tier per project",
    },
}


def default_limits_for_model(model: str) -> dict[str, Any]:
    m = model.strip().lower()
    if m in MODEL_FREE_LIMITS:
        return dict(MODEL_FREE_LIMITS[m])
    if "flash-lite" in m:
        return {"rpm": 15, "tpm": 250_000, "rpd": 500, "notes": "Inferred flash-lite free tier"}
    if "pro" in m or "argon" in m or "carbon" in m:
        return {"rpm": 5, "tpm": 250_000, "rpd": 100, "notes": "Inferred pro-tier defaults (check AI Studio if paid-only)"}
    return {"rpm": 5, "tpm": 250_000, "rpd": 20, "notes": "Conservative free-tier defaults (5 RPM / 250K TPM / 20 RPD)"}


def pt_today_str(now_utc: datetime | None = None) -> str:
    dt = now_utc or datetime.now(timezone.utc)
    return dt.astimezone(PT_TZ).strftime("%Y-%m-%d")


def next_pt_reset_info(now_utc: datetime | None = None) -> tuple[datetime, timedelta, str]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(PT_TZ)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = tomorrow - now
    local_reset = tomorrow.astimezone()
    secs = int(delta.total_seconds())
    hh, rem = divmod(max(0, secs), 3600)
    mm, ss = divmod(rem, 60)
    return local_reset, delta, f"{hh:02d}h{mm:02d}m{ss:02d}s (local {local_reset.strftime('%Y-%m-%d %H:%M:%S %Z')})"


def key_fingerprint(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:12]


def mask_key(raw_key: str) -> str:
    s = raw_key.strip()
    if len(s) <= 10:
        return "*" * len(s)
    return f"{s[:6]}...{s[-4:]}"


def _parse_bool(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on", "y")


def parse_inline_meta(meta_text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tok in re.split(r"[\s,;]+", meta_text.strip()):
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        k = k.strip().lower()
        v = v.strip()
        if not v:
            continue
        if k in ("rpm", "tpm", "rpd", "reserve", "reserve_rpd"):
            out["reserve_rpd" if k == "reserve" else k] = int(v)
        elif k in ("cap", "daily_cap"):
            out["daily_cap"] = None if v.lower() == "none" else int(v)
        elif k == "weight":
            out["weight"] = float(v)
        elif k == "enabled":
            out["enabled"] = _parse_bool(v)
        elif k in ("project", "pool", "project_group"):
            out["project_group"] = v
        elif k in ("label", "name"):
            out["label"] = v
    return out


def parse_keys_input(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for i, item in enumerate(raw_items, 1):
        raw_key = str(item.get("key", "")).strip()
        if not raw_key:
            continue
        kid = key_fingerprint(raw_key)
        if kid in seen:
            continue
        spec = {
            "index": len(ordered) + 1,
            "key": raw_key,
            "key_id": kid,
            "masked": mask_key(raw_key),
            "label": item.get("label") or f"key-{len(ordered) + 1}",
            "project_group": item.get("project_group") or None,
            "rpm": item.get("rpm"),
            "tpm": item.get("tpm"),
            "rpd": item.get("rpd"),
            "reserve_rpd": item.get("reserve_rpd"),
            "daily_cap": item.get("daily_cap"),
            "weight": item.get("weight"),
            "enabled": item.get("enabled"),
        }
        seen[kid] = spec
        ordered.append(spec)
    return ordered


def load_key_specs(gp: Path | None = None, keys_file: str | None = None) -> tuple[list[dict[str, Any]], Path | None]:
    source_path: Path | None = None
    raw_items: list[dict[str, Any]] = []

    def parse_text_lines(text: str) -> list[dict[str, Any]]:
        res = []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            meta: dict[str, Any] = {}
            if "#" in s:
                kpart, comment = s.split("#", 1)
                s = kpart.strip()
                meta = parse_inline_meta(comment)
            if s:
                meta["key"] = s
                res.append(meta)
        return res

    if keys_file:
        kp = Path(keys_file).resolve()
        source_path = kp
        text = kp.read_text(encoding="utf-8")
        if kp.suffix.lower() == ".json":
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                loaded = loaded.get("keys", [])
            raw_items = [x if isinstance(x, dict) else {"key": str(x)} for x in loaded]
        else:
            raw_items = parse_text_lines(text)
    elif os.environ.get("GEMINI_API_KEYS"):
        raw = os.environ["GEMINI_API_KEYS"]
        for tok in re.split(r"[\r\n;]+", raw):
            t = tok.strip()
            if not t or t.startswith("#"):
                continue
            for sub in t.split(","):
                sub = sub.strip()
                if sub:
                    raw_items.append({"key": sub})
    else:
        candidates = []
        if gp is not None:
            candidates.append(gp / "api_keys.txt")
            candidates.append(gp / "api_keys.json")
        candidates.append(Path.cwd() / "api_keys.txt")
        candidates.append(Path.cwd() / "api_keys.json")
        for cand in candidates:
            if cand.exists():
                source_path = cand
                text = cand.read_text(encoding="utf-8")
                if cand.suffix.lower() == ".json":
                    loaded = json.loads(text)
                    raw_items = [x if isinstance(x, dict) else {"key": str(x)} for x in loaded]
                else:
                    raw_items = parse_text_lines(text)
                break

    return parse_keys_input(raw_items), source_path


def resolve_state_path(
    state_file: str | None = None,
    gp: Path | None = None,
    keys_file_path: Path | None = None,
) -> Path:
    if state_file:
        return Path(state_file).resolve()
    if gp is not None:
        return (gp / "key_quota_state.json").resolve()
    if keys_file_path is not None:
        return keys_file_path.with_suffix(".quota_state.json").resolve()
    return (Path.cwd() / "key_quota_state.json").resolve()


def resolve_gt_project_optional(project_arg: str | None) -> Path | None:
    if project_arg:
        p = Path(project_arg).resolve()
        if (p / "manifest.json").exists():
            return p
        if (p / "work" / "gt_project" / "manifest.json").exists():
            return p / "work" / "gt_project"
        if p.is_dir():
            return p
        return None
    cwd = Path.cwd().resolve()
    for p in (cwd, cwd / "work" / "gt_project"):
        if (p / "manifest.json").exists():
            return p
    return None


@dataclass
class KeyLease:
    key: str
    key_id: str
    key_index: int
    masked: str
    pool_id: str
    model: str
    est_tokens: int


def parse_google_429(err_body: str) -> dict[str, Any]:
    """Parse Google RPC QuotaFailure / RetryInfo from HTTP 429 response body."""
    info: dict[str, Any] = {
        "is_daily": False,
        "retry_delay_s": None,
        "quota_id": "",
        "quota_metric": "",
        "quota_value": None,
        "message": "",
    }
    try:
        payload = json.loads(err_body)
        err = payload.get("error", {})
        info["message"] = str(err.get("message", ""))
        for detail in err.get("details", []):
            dtype = str(detail.get("@type", ""))
            if "QuotaFailure" in dtype:
                for v in detail.get("violations", []):
                    qid = str(v.get("quotaId", ""))
                    qmetric = str(v.get("quotaMetric", ""))
                    qval = v.get("quotaValue")
                    info["quota_id"] = qid
                    info["quota_metric"] = qmetric
                    if qval is not None:
                        try:
                            info["quota_value"] = int(qval)
                        except (TypeError, ValueError):
                            pass
                    if "PerDay" in qid or qval in ("0", 0):
                        info["is_daily"] = True
            elif "RetryInfo" in dtype:
                rd = str(detail.get("retryDelay", ""))
                m = re.match(r"([0-9]+(?:\.[0-9]+)?)s", rd)
                if m:
                    info["retry_delay_s"] = float(m.group(1))
    except Exception:
        pass

    if not info["is_daily"] and ("PerDay" in err_body or '"quotaValue": "0"' in err_body or '"quotaValue":0' in err_body):
        info["is_daily"] = True
    if info["retry_delay_s"] is None:
        m = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", err_body, flags=re.IGNORECASE)
        if m:
            info["retry_delay_s"] = float(m.group(1))
    return info


class QuotaManager:
    """Thread-safe per-key & per-project quota allocator with persistent daily ledger."""

    def __init__(
        self,
        key_specs: list[dict[str, Any]],
        state_path: Path | None = None,
        default_model: str = "gemini-4-hydrogen",
        reserve_rpd_override: int | None = None,
    ) -> None:
        self.lock = threading.RLock()
        self.state_path = state_path
        self.default_model = default_model
        self.reserve_rpd_override = reserve_rpd_override
        self.specs: list[dict[str, Any]] = list(key_specs)
        self.raw_by_id: dict[str, str] = {s["key_id"]: s["key"] for s in self.specs}
        self.spec_by_id: dict[str, dict[str, Any]] = {s["key_id"]: s for s in self.specs}
        self.state: dict[str, Any] = {"version": 1, "keys": {}, "pools": {}}
        self._in_flight: dict[str, int] = {}
        self._in_flight_tokens: dict[str, int] = {}
        self._load_and_sync_state()

    @classmethod
    def from_args(
        cls,
        gp: Path | None = None,
        keys_file: str | None = None,
        state_file: str | None = None,
        model: str = "gemini-4-hydrogen",
        reserve_rpd: int | None = None,
        require_keys: bool = True,
    ) -> "QuotaManager":
        specs, src_path = load_key_specs(gp=gp, keys_file=keys_file)
        if require_keys and not specs:
            raise RuntimeError(
                "No API keys found. Pass --keys-file <api_keys.txt>, set GEMINI_API_KEYS, or create api_keys.txt."
            )
        spath = resolve_state_path(state_file=state_file, gp=gp, keys_file_path=src_path)
        return cls(specs, state_path=spath, default_model=model, reserve_rpd_override=reserve_rpd)

    def _default_pool_id(self, spec: dict[str, Any], saved_key: dict[str, Any]) -> str:
        pg = spec.get("project_group") or saved_key.get("project_group")
        if pg:
            return f"project:{pg}"
        return f"key:{spec['key_id']}"

    def _ensure_pool_unlocked(self, pool_id: str, model: str) -> dict[str, Any]:
        pools = self.state.setdefault("pools", {})
        p = pools.get(pool_id)
        if not isinstance(p, dict):
            p = {
                "pool_id": pool_id,
                "pt_date": pt_today_str(),
                "models": {},
                "lifetime": {"requests": 0, "ok": 0, "err_429": 0, "prompt_tokens": 0, "candidates_tokens": 0, "thoughts_tokens": 0, "total_tokens": 0},
            }
            pools[pool_id] = p

        # Roll over if Pacific Time day changed
        today_pt = pt_today_str()
        if p.get("pt_date") != today_pt:
            p["pt_date"] = today_pt
            for mrec in p.get("models", {}).values():
                mrec["daily_used"] = 0
                mrec["daily_exhausted"] = False
                mrec["cool_until"] = 0.0
                mrec["recent_calls"] = []
                mrec["daily_tokens"] = {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0}
                mrec["last_error"] = ""

        models = p.setdefault("models", {})
        mrec = models.get(model)
        if not isinstance(mrec, dict):
            defaults = default_limits_for_model(model)
            mrec = {
                "rpm": defaults["rpm"],
                "tpm": defaults["tpm"],
                "rpd": defaults["rpd"],
                "daily_used": 0,
                "daily_exhausted": False,
                "cool_until": 0.0,
                "last_used_epoch": 0.0,
                "recent_calls": [],
                "daily_tokens": {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0},
                "last_error": "",
            }
            models[model] = mrec

        # Prune > 60s rolling entries
        cutoff = time.time() - 60.0
        mrec["recent_calls"] = [c for c in mrec.get("recent_calls", []) if float(c[0]) >= cutoff]
        return mrec

    def _load_and_sync_state(self) -> None:
        with self.lock:
            if self.state_path and self.state_path.exists():
                try:
                    loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        self.state = loaded
                except Exception:
                    pass
            self.state.setdefault("version", 1)
            keys_cfg = self.state.setdefault("keys", {})
            self.state.setdefault("pools", {})

            for spec in self.specs:
                kid = spec["key_id"]
                rec = keys_cfg.get(kid, {})
                if not isinstance(rec, dict):
                    rec = {}
                rec["key_id"] = kid
                rec["masked"] = spec["masked"]
                rec["index"] = spec["index"]
                if spec.get("label") and (not rec.get("label") or rec["label"].startswith("key-")):
                    rec["label"] = spec["label"]
                else:
                    rec.setdefault("label", spec["label"])
                if spec.get("project_group") is not None:
                    rec["project_group"] = spec["project_group"]
                else:
                    rec.setdefault("project_group", None)
                if spec.get("enabled") is not None:
                    rec["enabled"] = bool(spec["enabled"])
                else:
                    rec.setdefault("enabled", True)
                if spec.get("weight") is not None:
                    rec["weight"] = float(spec["weight"])
                else:
                    rec.setdefault("weight", 1.0)
                if spec.get("reserve_rpd") is not None:
                    rec["reserve_rpd"] = int(spec["reserve_rpd"])
                else:
                    rec.setdefault("reserve_rpd", 0)
                if "daily_cap" in spec and spec["daily_cap"] is not None:
                    rec["daily_cap"] = int(spec["daily_cap"])
                else:
                    rec.setdefault("daily_cap", None)
                rec.setdefault("invalid", False)
                rec.setdefault("invalid_reason", "")
                keys_cfg[kid] = rec

                pid = self._default_pool_id(spec, rec)
                rec["pool_id"] = pid
                mrec = self._ensure_pool_unlocked(pid, self.default_model)
                for lim_k in ("rpm", "tpm", "rpd"):
                    if spec.get(lim_k) is not None:
                        mrec[lim_k] = int(spec[lim_k])

            if self.state_path:
                self._save_unlocked()

    def _save_unlocked(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _effective_budget(self, krec: dict[str, Any], mrec: dict[str, Any], pool_id: str) -> dict[str, Any]:
        rpd = int(mrec.get("rpd", 20))
        rpm = int(mrec.get("rpm", 5))
        tpm = int(mrec.get("tpm", 250_000))
        reserve = self.reserve_rpd_override if self.reserve_rpd_override is not None else int(krec.get("reserve_rpd", 0))
        daily_cap = krec.get("daily_cap")
        allocatable = max(0, rpd - max(0, reserve))
        if daily_cap is not None:
            allocatable = min(allocatable, max(0, int(daily_cap)))

        daily_used = int(mrec.get("daily_used", 0))
        in_flight = self._in_flight.get(pool_id, 0)
        exhausted = bool(mrec.get("daily_exhausted", False))
        remaining_rpd = 0 if exhausted else max(0, allocatable - daily_used - in_flight)

        recent = mrec.get("recent_calls", [])
        used_rpm_60s = len(recent) + in_flight
        used_tpm_60s = sum(int(c[1]) for c in recent) + self._in_flight_tokens.get(pool_id, 0)
        return {
            "rpd": rpd,
            "rpm": rpm,
            "tpm": tpm,
            "reserve": reserve,
            "daily_cap": daily_cap,
            "allocatable_rpd": allocatable,
            "daily_used": daily_used,
            "in_flight": in_flight,
            "remaining_rpd": remaining_rpd,
            "exhausted": exhausted,
            "used_rpm_60s": used_rpm_60s,
            "used_tpm_60s": used_tpm_60s,
            "cool_until": float(mrec.get("cool_until", 0.0)),
            "last_used_epoch": float(mrec.get("last_used_epoch", 0.0)),
        }

    def acquire(
        self,
        model: str,
        est_tokens: int = 4000,
        preferred_kid: str | None = None,
        max_wait_s: float = 35.0,
    ) -> KeyLease | None:
        """Select and reserve a quota slot for one request, respecting RPM/TPM/RPD."""
        deadline = time.monotonic() + max_wait_s
        while True:
            wait_needed: float | None = None
            with self.lock:
                now = time.time()
                candidates: list[tuple[tuple[float, int, float, int], dict[str, Any], dict[str, Any], str]] = []
                soonest_ready_at: float | None = None
                has_non_exhausted = False

                for spec in self.specs:
                    kid = spec["key_id"]
                    krec = self.state["keys"][kid]
                    if not krec.get("enabled", True) or krec.get("invalid", False):
                        continue
                    pool_id = krec["pool_id"]
                    mrec = self._ensure_pool_unlocked(pool_id, model)
                    b = self._effective_budget(krec, mrec, pool_id)
                    if b["remaining_rpd"] <= 0:
                        continue
                    has_non_exhausted = True

                    # Determine earliest availability respecting cool_until, 60s RPM, and 60s TPM
                    ready_at = max(now, b["cool_until"])
                    recent = sorted(mrec.get("recent_calls", []), key=lambda x: float(x[0]))
                    if b["used_rpm_60s"] >= b["rpm"]:
                        if recent:
                            slot_idx = max(0, len(recent) - b["rpm"])
                            ready_at = max(ready_at, float(recent[slot_idx][0]) + 60.5)
                        else:
                            ready_at = max(ready_at, now + 5.0)
                    if b["used_tpm_60s"] > 0 and (b["used_tpm_60s"] + est_tokens) > b["tpm"] and recent:
                        # Wait until enough 60s tokens expire
                        cum = b["used_tpm_60s"]
                        for ts, tok in recent:
                            cum -= int(tok)
                            ready_at = max(ready_at, float(ts) + 60.5)
                            if (cum + est_tokens) <= b["tpm"]:
                                break

                    if ready_at <= now + 0.05:
                        weight = max(0.01, float(krec.get("weight", 1.0)))
                        ratio = (b["remaining_rpd"] / max(1, b["allocatable_rpd"])) * weight
                        prefer_bonus = 1 if (preferred_kid and kid != preferred_kid) else 0
                        # Higher score is picked: sort ascending by (-ratio, prefer_bonus, used_rpm_60s, last_used_epoch, index)
                        score = (-ratio, prefer_bonus, b["used_rpm_60s"], b["last_used_epoch"], spec["index"])
                        candidates.append((score, spec, mrec, pool_id))
                    else:
                        if soonest_ready_at is None or ready_at < soonest_ready_at:
                            soonest_ready_at = ready_at

                if candidates:
                    candidates.sort(key=lambda x: x[0])
                    _, chosen_spec, chosen_mrec, pool_id = candidates[0]
                    chosen_mrec["last_used_epoch"] = now
                    self._in_flight[pool_id] = self._in_flight.get(pool_id, 0) + 1
                    self._in_flight_tokens[pool_id] = self._in_flight_tokens.get(pool_id, 0) + est_tokens
                    return KeyLease(
                        key=chosen_spec["key"],
                        key_id=chosen_spec["key_id"],
                        key_index=chosen_spec["index"],
                        masked=chosen_spec["masked"],
                        pool_id=pool_id,
                        model=model,
                        est_tokens=est_tokens,
                    )

                if not has_non_exhausted:
                    return None

                if soonest_ready_at is None:
                    return None
                wait_needed = max(0.2, soonest_ready_at - now)

            rem_budget = deadline - time.monotonic()
            if rem_budget <= 0:
                return None
            time.sleep(min(wait_needed, rem_budget, 15.0))

    def _release_in_flight(self, lease: KeyLease) -> None:
        self._in_flight[lease.pool_id] = max(0, self._in_flight.get(lease.pool_id, 0) - 1)
        self._in_flight_tokens[lease.pool_id] = max(
            0, self._in_flight_tokens.get(lease.pool_id, 0) - lease.est_tokens
        )

    def record_success(self, lease: KeyLease, usage_metadata: dict[str, Any] | None = None) -> None:
        with self.lock:
            self._release_in_flight(lease)
            now = time.time()
            mrec = self._ensure_pool_unlocked(lease.pool_id, lease.model)
            mrec["daily_used"] = int(mrec.get("daily_used", 0)) + 1
            mrec["cool_until"] = 0.0
            mrec["last_error"] = ""

            um = usage_metadata or {}
            pt = int(um.get("promptTokenCount", 0) or 0)
            ct = int(um.get("candidatesTokenCount", 0) or 0)
            tt = int(um.get("thoughtsTokenCount", 0) or 0)
            tot = int(um.get("totalTokenCount", 0) or (pt + ct + tt) or lease.est_tokens)

            mrec.setdefault("recent_calls", []).append([now, tot])
            dtok = mrec.setdefault("daily_tokens", {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0})
            dtok["prompt"] += pt
            dtok["candidates"] += ct
            dtok["thoughts"] += tt
            dtok["total"] += tot

            lt = self.state["pools"][lease.pool_id].setdefault(
                "lifetime", {"requests": 0, "ok": 0, "err_429": 0, "prompt_tokens": 0, "candidates_tokens": 0, "thoughts_tokens": 0, "total_tokens": 0}
            )
            lt["requests"] += 1
            lt["ok"] += 1
            lt["prompt_tokens"] += pt
            lt["candidates_tokens"] += ct
            lt["thoughts_tokens"] += tt
            lt["total_tokens"] += tot
            self._save_unlocked()

    def record_429(self, lease: KeyLease, err_body: str) -> dict[str, Any]:
        parsed = parse_google_429(err_body)
        with self.lock:
            self._release_in_flight(lease)
            now = time.time()
            mrec = self._ensure_pool_unlocked(lease.pool_id, lease.model)
            lt = self.state["pools"][lease.pool_id]["lifetime"]
            lt["requests"] += 1
            lt["err_429"] += 1

            if parsed["is_daily"]:
                mrec["daily_exhausted"] = True
                if parsed["quota_value"] and parsed["quota_value"] > 0:
                    mrec["rpd"] = parsed["quota_value"]
                mrec["last_error"] = f"PerDay 429 ({parsed.get('quota_id') or 'daily exhausted'})"
            else:
                delay = float(parsed["retry_delay_s"] or 20.0) + 1.5
                mrec["cool_until"] = max(float(mrec.get("cool_until", 0.0)), now + delay)
                mrec["last_error"] = f"Rate 429 wait {delay:.0f}s ({parsed.get('quota_id') or 'RPM/TPM'})"
            self._save_unlocked()
        return parsed

    def record_invalid(self, lease: KeyLease, code: int, err_body: str) -> None:
        with self.lock:
            self._release_in_flight(lease)
            krec = self.state["keys"].get(lease.key_id)
            if krec is not None:
                krec["invalid"] = True
                krec["invalid_reason"] = f"HTTP_{code}: {err_body[:160]}"
            mrec = self._ensure_pool_unlocked(lease.pool_id, lease.model)
            mrec["last_error"] = f"HTTP_{code} key invalid/denied"
            self._save_unlocked()

    def record_transient_release(self, lease: KeyLease, reason: str = "") -> None:
        with self.lock:
            self._release_in_flight(lease)
            if reason:
                mrec = self._ensure_pool_unlocked(lease.pool_id, lease.model)
                mrec["last_error"] = reason[:160]
                self._save_unlocked()

    def total_remaining_rpd(self, model: str | None = None) -> int:
        m = model or self.default_model
        with self.lock:
            seen_pools: set[str] = set()
            total = 0
            for spec in self.specs:
                krec = self.state["keys"][spec["key_id"]]
                if not krec.get("enabled", True) or krec.get("invalid", False):
                    continue
                pid = krec["pool_id"]
                if pid in seen_pools:
                    continue
                seen_pools.add(pid)
                mrec = self._ensure_pool_unlocked(pid, m)
                b = self._effective_budget(krec, mrec, pid)
                total += b["remaining_rpd"]
            return total

    def get_snapshot(self, model: str | None = None) -> list[dict[str, Any]]:
        m = model or self.default_model
        now = time.time()
        rows: list[dict[str, Any]] = []
        with self.lock:
            for spec in self.specs:
                kid = spec["key_id"]
                krec = self.state["keys"][kid]
                pid = krec["pool_id"]
                mrec = self._ensure_pool_unlocked(pid, m)
                b = self._effective_budget(krec, mrec, pid)
                dtok = mrec.get("daily_tokens", {})
                if not krec.get("enabled", True):
                    status = "DISABLED"
                elif krec.get("invalid", False):
                    status = "INVALID"
                elif b["exhausted"] or b["remaining_rpd"] <= 0:
                    status = "DAILY_EXHAUSTED"
                elif b["cool_until"] > now:
                    status = f"COOL({int(b['cool_until'] - now)}s)"
                elif b["used_rpm_60s"] >= b["rpm"]:
                    status = f"RPM_WAIT({b['used_rpm_60s']}/{b['rpm']})"
                else:
                    status = "READY"
                rows.append(
                    {
                        "index": spec["index"],
                        "key_id": kid,
                        "label": krec.get("label", spec["label"]),
                        "masked": spec["masked"],
                        "pool_id": pid,
                        "status": status,
                        "daily_used": b["daily_used"],
                        "allocatable_rpd": b["allocatable_rpd"],
                        "remaining_rpd": b["remaining_rpd"],
                        "rpd": b["rpd"],
                        "reserve": b["reserve"],
                        "daily_cap": b["daily_cap"],
                        "rpm_60s": f"{b['used_rpm_60s']}/{b['rpm']}",
                        "tpm_60s": f"{b['used_tpm_60s']}/{b['tpm']}",
                        "weight": float(krec.get("weight", 1.0)),
                        "tokens_today": dtok,
                        "last_error": mrec.get("last_error", ""),
                    }
                )
        return rows

    def plan_workload(self, total_requests: int, model: str | None = None, chunk_names: list[str] | None = None) -> dict[str, Any]:
        m = model or self.default_model
        with self.lock:
            rows = self.get_snapshot(m)
            # Group by pool_id so keys sharing a project don't get double-counted
            pool_seen: dict[str, dict[str, Any]] = {}
            duplicate_project_keys: list[dict[str, Any]] = []
            for r in rows:
                pid = r["pool_id"]
                if pid in pool_seen:
                    duplicate_project_keys.append(r)
                    continue
                if r["status"] not in ("DISABLED", "INVALID"):
                    pool_seen[pid] = dict(r)

            eligible = sorted(
                pool_seen.values(),
                key=lambda x: (-x["remaining_rpd"] * x["weight"], x["index"]),
            )
            allocations: list[dict[str, Any]] = []
            cursor = 0
            remaining_work = max(0, total_requests)
            total_avail = sum(e["remaining_rpd"] for e in eligible)

            if total_avail > 0 and remaining_work > 0:
                # Fair water-filling proportional to (remaining_rpd * weight), capped by remaining_rpd
                raw_shares: dict[int, int] = {}
                target_total = min(remaining_work, total_avail)
                weight_sum = sum(e["remaining_rpd"] * e["weight"] for e in eligible if e["remaining_rpd"] > 0)
                assigned = 0
                for e in eligible:
                    if e["remaining_rpd"] <= 0:
                        raw_shares[e["index"]] = 0
                        continue
                    exact = target_total * (e["remaining_rpd"] * e["weight"]) / max(1e-9, weight_sum)
                    cnt = min(e["remaining_rpd"], int(exact))
                    raw_shares[e["index"]] = cnt
                    assigned += cnt
                leftover = target_total - assigned
                for e in eligible:
                    if leftover <= 0:
                        break
                    room = e["remaining_rpd"] - raw_shares[e["index"]]
                    if room > 0:
                        give = min(room, leftover)
                        raw_shares[e["index"]] += give
                        leftover -= give

                for e in eligible:
                    cnt = raw_shares.get(e["index"], 0)
                    assigned_chunks = chunk_names[cursor : cursor + cnt] if (chunk_names and cnt > 0) else []
                    cursor += cnt
                    allocations.append(
                        {
                            "index": e["index"],
                            "label": e["label"],
                            "masked": e["masked"],
                            "pool_id": e["pool_id"],
                            "remaining_rpd": e["remaining_rpd"],
                            "allocated": cnt,
                            "post_remaining_rpd": e["remaining_rpd"] - cnt,
                            "chunks": assigned_chunks,
                        }
                    )

            return {
                "model": m,
                "pt_date": pt_today_str(),
                "total_requested": total_requests,
                "total_available_rpd": total_avail,
                "can_complete_today": total_avail >= total_requests,
                "shortfall": max(0, total_requests - total_avail),
                "unique_pools": len(pool_seen),
                "duplicate_project_keys": [d["masked"] for d in duplicate_project_keys],
                "allocations": allocations,
            }

    def set_limits(
        self,
        target: str,
        model: str,
        rpm: int | None = None,
        tpm: int | None = None,
        rpd: int | None = None,
        reserve_rpd: int | None = None,
        daily_cap: int | None = None,
        clear_cap: bool = False,
        weight: float | None = None,
        enabled: bool | None = None,
        project_group: str | None = None,
        label: str | None = None,
    ) -> list[str]:
        updated: list[str] = []
        with self.lock:
            for spec in self.specs:
                if not _matches_key_selector(target, spec, self.state["keys"][spec["key_id"]]):
                    continue
                kid = spec["key_id"]
                krec = self.state["keys"][kid]
                if project_group is not None:
                    krec["project_group"] = project_group or None
                    krec["pool_id"] = self._default_pool_id(spec, krec)
                if label is not None:
                    krec["label"] = label
                if reserve_rpd is not None:
                    krec["reserve_rpd"] = max(0, reserve_rpd)
                if clear_cap:
                    krec["daily_cap"] = None
                elif daily_cap is not None:
                    krec["daily_cap"] = max(0, daily_cap)
                if weight is not None:
                    krec["weight"] = max(0.01, weight)
                if enabled is not None:
                    krec["enabled"] = enabled

                mrec = self._ensure_pool_unlocked(krec["pool_id"], model)
                if rpm is not None:
                    mrec["rpm"] = max(1, rpm)
                if tpm is not None:
                    mrec["tpm"] = max(1000, tpm)
                if rpd is not None:
                    mrec["rpd"] = max(0, rpd)
                    if mrec["daily_used"] < mrec["rpd"]:
                        mrec["daily_exhausted"] = False
                updated.append(f"#{spec['index']} {spec['masked']} ({krec['label']})")
            self._save_unlocked()
        return updated

    def reset_keys(
        self,
        target: str,
        model: str,
        reset_daily: bool = False,
        reset_cooldown: bool = True,
        reset_invalid: bool = False,
    ) -> list[str]:
        updated: list[str] = []
        with self.lock:
            for spec in self.specs:
                kid = spec["key_id"]
                krec = self.state["keys"][kid]
                if not _matches_key_selector(target, spec, krec):
                    continue
                if reset_invalid:
                    krec["invalid"] = False
                    krec["invalid_reason"] = ""
                mrec = self._ensure_pool_unlocked(krec["pool_id"], model)
                if reset_cooldown:
                    mrec["cool_until"] = 0.0
                    mrec["last_error"] = ""
                if reset_daily:
                    mrec["daily_used"] = 0
                    mrec["daily_exhausted"] = False
                    mrec["daily_tokens"] = {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0}
                updated.append(f"#{spec['index']} {spec['masked']} ({krec['label']})")
            self._save_unlocked()
        return updated


def _matches_key_selector(target: str, spec: dict[str, Any], krec: dict[str, Any]) -> bool:
    t = target.strip()
    if t.lower() == "all":
        return True
    if t.startswith("#") and t[1:].isdigit() and int(t[1:]) == spec["index"]:
        return True
    if t.isdigit() and int(t) == spec["index"]:
        return True
    return t in (spec["key_id"], spec["masked"], krec.get("label", ""))


def format_status_report(mgr: QuotaManager, model: str) -> str:
    _, _, reset_str = next_pt_reset_info()
    rows = mgr.get_snapshot(model)
    preset = default_limits_for_model(model)
    lines = [
        f"Model: {model} | PT Date: {pt_today_str()} | Next Reset: {reset_str}",
        f"Reference Free Tier: {preset['rpm']} RPM | {preset['tpm']:,} TPM | {preset['rpd']} RPD ({preset['notes']})",
        f"State file: {mgr.state_path}",
    ]
    if not rows:
        lines.append("No API keys configured yet. Pass --keys-file <api_keys.txt> or set GEMINI_API_KEYS.")
        return "\n".join(lines)

    seen_pools: set[str] = set()
    total_rem = 0
    total_cap = 0
    for r in rows:
        if r["pool_id"] not in seen_pools and r["status"] not in ("DISABLED", "INVALID"):
            seen_pools.add(r["pool_id"])
            total_rem += r["remaining_rpd"]
            total_cap += r["allocatable_rpd"]
        tok = r["tokens_today"]
        tok_str = f"tot={tok.get('total', 0):,}(thk={tok.get('thoughts', 0):,})"
        err = f" | err={r['last_error']}" if r["last_error"] else ""
        lines.append(
            f"  [#{r['index']} {r['label']} | {r['masked']} | {r['pool_id']}] "
            f"{r['status']} | RPD {r['daily_used']}/{r['allocatable_rpd']} (rem={r['remaining_rpd']}, rsv={r['reserve']}) "
            f"| 60s RPM {r['rpm_60s']} TPM {r['tpm_60s']} | {tok_str}{err}"
        )
    lines.append(
        f"Pool Summary: {len(rows)} keys across {len(seen_pools)} active pools | Total Allocatable Remaining Today: {total_rem}/{total_cap} RPD"
    )
    return "\n".join(lines)


def check_models_zero_cost(mgr: QuotaManager, target_model: str) -> None:
    """Verify key validity & model access using GET /v1beta/models (0 generateContent RPD cost)."""
    print(f"Checking key validity & model access for `{target_model}` via GET /v1beta/models/{target_model} (0 RPD used)...")
    for spec in mgr.specs:
        kid = spec["key_id"]
        key = spec["key"]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}?key={key}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            in_lim = data.get("inputTokenLimit", "?")
            out_lim = data.get("outputTokenLimit", "?")
            print(f"  [OK] #{spec['index']} {spec['masked']}: model={data.get('name', target_model)} in_limit={in_lim} out_limit={out_lim}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:260]
            if e.code in (400, 403):
                with mgr.lock:
                    mgr.state["keys"][kid]["invalid"] = True
                    mgr.state["keys"][kid]["invalid_reason"] = f"HTTP_{e.code}: {body[:140]}"
                    mgr._save_unlocked()
            print(f"  [ERR] #{spec['index']} {spec['masked']}: HTTP {e.code} | {body}")
        except Exception as exc:
            print(f"  [ERR] #{spec['index']} {spec['masked']}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Google AI Studio per-key & per-project quotas")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", help="Optional gt_project or game root")
        p.add_argument("--keys-file", help="Path to api_keys.txt or api_keys.json")
        p.add_argument("--state-file", help="Override path to key_quota_state.json")
        p.add_argument("--model", default="gemini-4-hydrogen")

    p_stat = sub.add_parser("status", help="Show per-key quota status & Pacific Time reset countdown")
    add_common(p_stat)

    p_set = sub.add_parser("set-limit", help="Configure per-key/pool RPM, TPM, RPD, reserve, cap, label, or project-group")
    add_common(p_set)
    p_set.add_argument("--key", default="all", help="Key selector (#1, 1, key_id, masked, label, or 'all')")
    p_set.add_argument("--rpm", type=int)
    p_set.add_argument("--tpm", type=int)
    p_set.add_argument("--rpd", type=int)
    p_set.add_argument("--reserve", type=int, help="Daily RPD safety reserve untouched on each key")
    p_set.add_argument("--cap", type=int, help="Hard max daily allocations per key")
    p_set.add_argument("--clear-cap", action="store_true")
    p_set.add_argument("--weight", type=float)
    p_set.add_argument("--project-group", help="Group multiple keys sharing one GCP project quota")
    p_set.add_argument("--label", help="Human-friendly key label")
    grp = p_set.add_mutually_exclusive_group()
    grp.add_argument("--enable", action="store_true")
    grp.add_argument("--disable", action="store_true")

    p_plan = sub.add_parser("plan", help="Compute fair per-key chunk/request allocation plan")
    add_common(p_plan)
    p_plan.add_argument("--requests", type=int, help="Number of requests/chunks to allocate (auto-detected if --project given)")
    p_plan.add_argument("--reserve-rpd", type=int, help="Temporary reserve override")
    p_plan.add_argument("--json", action="store_true", help="Output JSON allocation plan")

    p_chk = sub.add_parser("check-models", help="Check key validity & model availability without spending generateContent RPD")
    add_common(p_chk)

    p_rst = sub.add_parser("reset", help="Reset cooldown, invalid flag, or daily counters")
    add_common(p_rst)
    p_rst.add_argument("--key", default="all")
    p_rst.add_argument("--daily", action="store_true")
    p_rst.add_argument("--cooldown", action="store_true")
    p_rst.add_argument("--invalid", action="store_true")

    args = parser.parse_args()
    gp = resolve_gt_project_optional(args.project)
    require_keys = args.cmd != "status"
    try:
        mgr = QuotaManager.from_args(
            gp=gp,
            keys_file=args.keys_file,
            state_file=args.state_file,
            model=args.model,
            reserve_rpd=getattr(args, "reserve_rpd", None),
            require_keys=require_keys,
        )
    except RuntimeError as exc:
        if args.cmd == "status":
            mgr = QuotaManager([], state_path=resolve_state_path(args.state_file, gp, None), default_model=args.model)
        else:
            raise SystemExit(f"Error: {exc}")

    if args.cmd == "status":
        print(format_status_report(mgr, args.model))
    elif args.cmd == "set-limit":
        en = True if args.enable else (False if args.disable else None)
        changed = mgr.set_limits(
            args.key,
            args.model,
            rpm=args.rpm,
            tpm=args.tpm,
            rpd=args.rpd,
            reserve_rpd=args.reserve,
            daily_cap=args.cap,
            clear_cap=args.clear_cap,
            weight=args.weight,
            enabled=en,
            project_group=args.project_group,
            label=args.label,
        )
        print(f"Updated {len(changed)} key(s): {', '.join(changed)}")
        print(format_status_report(mgr, args.model))
    elif args.cmd == "plan":
        chunk_names: list[str] | None = None
        total_reqs = args.requests
        if total_reqs is None and gp and (gp / "manifest.json").exists():
            manifest = json.loads((gp / "manifest.json").read_text(encoding="utf-8"))
            chunks_out = gp / "chunks_out"
            skipped_path = gp / "skipped_chunks.json"
            skipped = set(json.loads(skipped_path.read_text(encoding="utf-8"))) if skipped_path.exists() else set()
            chunk_names = [m["chunk"] for m in manifest if not (chunks_out / m["chunk"]).exists() and m["chunk"] not in skipped]
            total_reqs = len(chunk_names)
        if total_reqs is None:
            raise SystemExit("Pass --requests N or --project <gt_project> containing manifest.json.")
        plan = mgr.plan_workload(total_reqs, model=args.model, chunk_names=chunk_names)
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return
        print(
            f"Allocation Plan ({plan['model']} | PT {plan['pt_date']}): "
            f"needed={plan['total_requested']} reqs, available={plan['total_available_rpd']} RPD "
            f"across {plan['unique_pools']} pool(s)"
        )
        if plan["duplicate_project_keys"]:
            print(f"  [WARN] Shared-project keys excluded from double-counting: {plan['duplicate_project_keys']}")
        for a in plan["allocations"]:
            chunk_span = f" -> chunks {a['chunks'][0]}..{a['chunks'][-1]}" if a["chunks"] else ""
            print(
                f"  [#{a['index']} {a['label']} ({a['masked']}) | {a['pool_id']}] "
                f"alloc={a['allocated']} / rem={a['remaining_rpd']} (after={a['post_remaining_rpd']}){chunk_span}"
            )
        if not plan["can_complete_today"]:
            _, _, reset_str = next_pt_reset_info()
            print(f"  [SHORTFALL] {plan['shortfall']} req(s) cannot finish today; reset in {reset_str}.")
    elif args.cmd == "check-models":
        check_models_zero_cost(mgr, args.model)
    elif args.cmd == "reset":
        changed = mgr.reset_keys(
            args.key,
            args.model,
            reset_daily=args.daily,
            reset_cooldown=args.cooldown or (not args.daily and not args.invalid),
            reset_invalid=args.invalid,
        )
        print(f"Reset {len(changed)} key(s): {', '.join(changed)}")
        print(format_status_report(mgr, args.model))


if __name__ == "__main__":
    main()
