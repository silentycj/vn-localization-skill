"""Translate VN script chunks via Google AI Studio REST API (quota-managed multi-key allocation).

Reads keys from `--keys-file`, `GEMINI_API_KEYS` env var, or `<gt_project>/api_keys.txt`.
Integrates with `key_quota_manager.py` for per-key / per-project RPM, TPM, and daily RPD
allocation, `00:00 PT` daily reset, and `usageMetadata` (including thinking tokens) tracking.
Never store real API keys inside the skill directory.

Usage:
    python api_translate_wave.py [--project <gt_project>] [--keys-file <api_keys.txt>] \
        [--model gemini-4-hydrogen] [--thinking-level HIGH] [--batch-size 18] [--concurrency 4]
"""

import argparse
import concurrent.futures
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import key_quota_manager as kqm

_QUOTA_MGR: kqm.QuotaManager | None = None
_MGR_INIT_LOCK = threading.Lock()

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "idx": {"type": "INTEGER"},
                    "name": {"type": "STRING"},
                    "message": {"type": "STRING"},
                },
                "required": ["idx", "message"],
            },
        }
    },
    "required": ["items"],
}


def resolve_gt_project(project_arg: str | None) -> Path:
    if project_arg:
        p = Path(project_arg).resolve()
        if (p / "manifest.json").exists():
            return p
        if (p / "work" / "gt_project" / "manifest.json").exists():
            return p / "work" / "gt_project"
    cwd = Path.cwd().resolve()
    for p in (cwd, cwd / "work" / "gt_project"):
        if (p / "manifest.json").exists():
            return p
    for cand in sorted(cwd.glob("*/work/gt_project/manifest.json")):
        return cand.parent
    raise FileNotFoundError("Could not locate gt_project containing manifest.json; pass --project.")


def init_keys(
    gp: Path | None,
    keys_file: str | None = None,
    state_file: str | None = None,
    model: str = "gemini-4-hydrogen",
    reserve_rpd: int | None = None,
) -> list[str]:
    global _QUOTA_MGR
    with _MGR_INIT_LOCK:
        _QUOTA_MGR = kqm.QuotaManager.from_args(
            gp=gp,
            keys_file=keys_file,
            state_file=state_file,
            model=model,
            reserve_rpd=reserve_rpd,
            require_keys=True,
        )
        return [s["key"] for s in _QUOTA_MGR.specs]


def get_quota_manager() -> kqm.QuotaManager:
    if _QUOTA_MGR is None:
        raise RuntimeError("QuotaManager not initialized; call init_keys() first.")
    return _QUOTA_MGR


def load_glossary(gp: Path) -> tuple[dict[str, str], str]:
    glossary_path = gp / "glossary.json"
    if not glossary_path.exists():
        return {}, ""
    data = json.loads(glossary_path.read_text(encoding="utf-8"))
    return dict(data.get("name_map", {})), str(data.get("glossary", "")).strip()


def call_gemini(
    model: str,
    prompt: str,
    thinking_level: str | None = None,
    timeout: int = 240,
    raw_key: str = "",
) -> tuple[dict, dict]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={raw_key}"
    gen_cfg = {
        "temperature": 0.3,
        "responseMimeType": "application/json",
        "responseSchema": SCHEMA,
    }
    if thinking_level:
        gen_cfg["thinkingConfig"] = {"thinkingLevel": thinking_level.upper()}
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": gen_cfg,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    usage_meta = data.get("usageMetadata", {}) or {}
    pf = data.get("promptFeedback", {})
    if pf.get("blockReason"):
        err = RuntimeError(f"safety_prompt_blocked:{pf['blockReason']}")
        setattr(err, "usage_metadata", usage_meta)
        raise err
    cands = data.get("candidates", [])
    if not cands:
        err = RuntimeError("safety_no_candidates")
        setattr(err, "usage_metadata", usage_meta)
        raise err
    c0 = cands[0]
    fr = c0.get("finishReason", "")
    if fr in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
        err = RuntimeError(f"safety_finish_{fr}")
        setattr(err, "usage_metadata", usage_meta)
        raise err
    parts = c0.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        err = RuntimeError(f"empty_response_finish_{fr}")
        setattr(err, "usage_metadata", usage_meta)
        raise err
    try:
        return json.loads(text), usage_meta
    except Exception as exc:
        setattr(exc, "usage_metadata", usage_meta)
        raise


def filter_glossary_for_batch(orig: list[dict], glossary: str, name_map: dict[str, str]) -> str:
    blob_parts = [e.get("name", "") for e in orig if e.get("name")] + [e["message"] for e in orig]
    blob = "\n".join(blob_parts)
    parts: list[str] = []

    used_speakers = []
    seen_spk = set()
    for e in orig:
        sp = e.get("name", "")
        if sp and sp in name_map and sp not in seen_spk:
            seen_spk.add(sp)
            used_speakers.append(f"{sp}->{name_map[sp]}")
    if used_speakers:
        parts.append("Speakers: " + ", ".join(used_speakers))

    if glossary:
        kept_lines: list[str] = []
        pending_header: str | None = None
        for raw_line in glossary.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#") or (line.startswith("【") and line.endswith("】")):
                pending_header = line
                continue
            src_part = re.split(r"\s*(?:->|=>|→|:|：|=)\s*", line.lstrip("-*• "), maxsplit=1)[0]
            src_part = re.split(r"\s*[（(]", src_part, maxsplit=1)[0].strip()
            tokens = [t.strip() for t in re.split(r"[/／、,，]", src_part) if len(t.strip()) >= 1]
            if tokens and any(t in blob for t in tokens):
                if pending_header:
                    kept_lines.append(pending_header)
                    pending_header = None
                kept_lines.append(line)
        if kept_lines:
            parts.append("\n".join(kept_lines))

    return "\n".join(parts)


def encode_compact_payload(orig: list[dict]) -> str:
    rows = []
    for i, entry in enumerate(orig):
        spk = str(entry.get("name", "")).replace("\t", " ").replace("\r", "").replace("\n", "")
        msg = str(entry["message"]).replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
        rows.append(f"{i}\t{spk}\t{msg}")
    return "\n".join(rows)


def translate_lines(
    model: str,
    label: str,
    src_file: str,
    orig: list[dict],
    name_map: dict[str, str],
    glossary: str,
    thinking_level: str | None = None,
) -> list[dict]:
    payload_text = encode_compact_payload(orig)
    filtered_glossary = filter_glossary_for_batch(orig, glossary, name_map)
    glossary_block = f"GLOSSARY:\n{filtered_glossary}\n\n" if filtered_glossary else ""
    base_prompt = (
        f"Translate VN {label} ({src_file}, {len(orig)} lines) JP->Simplified Chinese (humanizer-zh):\n"
        "1. Keep ALL facts, tone, emotion, agency, negatives & nuances verbatim; no omissions/merges/inventions.\n"
        "2. Natural spoken Chinese matching speaker voice; avoid stacked 「的」, 「进行+V」, stiff 「被」, repetitive 「她/他」.\n"
        "3. Sensual, Anatomy & Organ-Action Fidelity: Keep full erotic tension, shame-arousal contrast, organ terms & physical organ actions vivid (avoid medical/textbook terms `粘膜/突起/分泌物/私处/子袋` or flat verbs `触碰/进入/刺激`). Match voice: heroine (`蜜穴/花瓣/花壁/花核/乳尖/子宫口/后庭`) vs antagonist (`淫穴/骚屄/肉棒/龟头/屁眼/奶头`) with visceral actions (`抠挖/揉弄/舔舐/吮吸/挺腰贯入/深抽浅送/抵住花心猛撞/痉挛紧咬/涌出淫水/喷洒精液`).\n"
        "4. Natural Chinese SFX/cries: Convert kana SFX into idiomatic Chinese sound/cadence (not ザアァ->沙啊啊, あぐっ->啊咕, ぎゃっ->啊嘎, ひぃっ->哈咿, or long stacks like 咕滋滋滋滋); crisp stutters; prefer …… / —— over ～～; zero leftover kana.\n"
        "5. Keep 「...」, （...）, ……, ——, tags ([r],[l],[p],{w},%s,%d,\\V[n],<...>) & \\r\\n breaks; strip [・・・/词] or [reading/base] ruby brackets to plain Chinese.\n"
        f"6. Input format is `idx\\tspeaker\\tmessage` ({len(orig)} lines). Return JSON `items` with EXACTLY {len(orig)} objects `{{idx, message}}` (0..{len(orig)-1}); omit `name` unless speaker is unmapped.\n"
        f"{glossary_block}"
        f"INPUT:\n{payload_text}"
    )

    mgr = get_quota_manager()
    est_tokens = max(512, len(base_prompt) + (3000 if thinking_level and thinking_level.upper() == "HIGH" else 1200))
    last_err = None
    last_kid: str | None = None
    content_attempts = 0
    max_attempts = max(16, len(mgr.specs) * 4)
    for _loop in range(max_attempts):
        lease = mgr.acquire(model, est_tokens=est_tokens, preferred_kid=last_kid)
        if lease is None:
            raise RuntimeError("quota_429_all_keys_daily_exhausted")
        last_kid = lease.key_id
        is_content_retry = content_attempts > 0 and last_err and not any(t in last_err for t in ("quota_429", "HTTP_50", "timed out", "remote end closed", "connection", "invalid"))
        prompt = base_prompt if not is_content_retry else f"{base_prompt}\n\nRETRY NOTE: Previous attempt failed ({last_err}). Output EXACTLY {len(orig)} items with `idx` 0..{len(orig)-1}."
        try:
            res, usage_meta = call_gemini(model, prompt, thinking_level=thinking_level, raw_key=lease.key)
            mgr.record_success(lease, usage_meta)
            got = res.get("items", [])
            if len(got) == 0:
                raise RuntimeError("empty_items_refused")
            if len(got) != len(orig):
                by_idx = {int(x["idx"]): x for x in got if isinstance(x, dict) and "idx" in x}
                if len(by_idx) == len(orig) and all(i in by_idx for i in range(len(orig))):
                    got = [by_idx[i] for i in range(len(orig))]
                else:
                    content_attempts += 1
                    if content_attempts >= 2:
                        raise ValueError(f"count_mismatch_{len(got)}_{len(orig)}")
                    last_err = f"count_mismatch_{len(got)}_{len(orig)}"
                    continue
            cleaned = []
            for i, (src_e, dst_e) in enumerate(zip(orig, got)):
                raw_src = src_e["message"]
                if not raw_src.strip():
                    msg = raw_src
                else:
                    msg = str(dst_e.get("message", "")).strip().replace("\\r\\n", "\r\n").replace("\\n", "\n").replace("\\t", "\t")
                    if not msg:
                        raise ValueError(f"empty_msg_at_{i}")
                out_e = {}
                if "name" in src_e:
                    raw_n = src_e["name"]
                    out_e["name"] = name_map.get(raw_n) or (str(dst_e.get("name", "")).strip() or raw_n)
                out_e["message"] = msg
                cleaned.append(out_e)
            return cleaned
        except urllib.error.HTTPError as e:
            try:
                raw_err = e.read()
            except Exception as read_exc:
                raw_err = getattr(read_exc, "partial", b"")
            err_body = raw_err.decode("utf-8", errors="replace")[:900]
            if e.code == 429:
                parsed = mgr.record_429(lease, err_body)
                kind = "PerDay" if parsed["is_daily"] else "RPM/TPM"
                last_err = f"quota_429(key#{lease.key_index} {kind}): {err_body[:260]}"
                continue
            if e.code in (400, 403) and ("API_KEY_INVALID" in err_body or "API key not valid" in err_body or "PERMISSION_DENIED" in err_body):
                mgr.record_invalid(lease, e.code, err_body)
                last_err = f"key_invalid(key#{lease.key_index}): HTTP_{e.code}"
                continue
            mgr.record_transient_release(lease, f"HTTP_{e.code}")
            if e.code in (500, 502, 503, 504):
                time.sleep(10)
                last_err = f"HTTP_{e.code}: {err_body[:200]}"
                continue
            content_attempts += 1
            last_err = f"HTTP_{e.code}: {err_body[:300]}"
            if content_attempts >= 2:
                break
        except Exception as exc:
            last_err = str(exc)
            um = getattr(exc, "usage_metadata", None)
            if um is not None:
                mgr.record_success(lease, um)
            else:
                mgr.record_transient_release(lease, last_err)
            if last_err.startswith("safety_") or "empty_items_refused" in last_err or "all_keys_daily" in last_err:
                raise
            if any(k in last_err.lower() for k in ("timed out", "remote end closed", "connection", "reset by peer")):
                time.sleep(5)
                continue
            content_attempts += 1
            if content_attempts >= 2:
                break
    raise RuntimeError(last_err or "unknown_error")


def prefill_from_tm(manifest: list[dict], chunks_in: Path, chunks_out: Path, name_map: dict[str, str]) -> int:
    tm: dict[tuple[str, str], tuple[str | None, str]] = {}
    for m in manifest:
        op = chunks_out / m["chunk"]
        if not op.exists():
            continue
        sr = json.loads((chunks_in / m["chunk"]).read_text(encoding="utf-8"))
        dr = json.loads(op.read_text(encoding="utf-8"))
        for s_ent, d_ent in zip(sr, dr):
            if d_ent.get("message") != s_ent.get("message"):
                tm.setdefault((s_ent.get("name", ""), s_ent["message"]), (d_ent.get("name"), d_ent["message"]))
    if not tm:
        return 0
    filled = 0
    for m in manifest:
        op = chunks_out / m["chunk"]
        if op.exists():
            continue
        sr = json.loads((chunks_in / m["chunk"]).read_text(encoding="utf-8"))
        if all((not s_ent["message"].strip()) or ((s_ent.get("name", ""), s_ent["message"]) in tm) for s_ent in sr):
            out_rows = []
            for s_ent in sr:
                if not s_ent["message"].strip():
                    out_rows.append(dict(s_ent))
                    continue
                tn, tmsg = tm[(s_ent.get("name", ""), s_ent["message"])]
                row = {}
                if "name" in s_ent:
                    raw_n = s_ent["name"]
                    row["name"] = name_map.get(raw_n) or tn or raw_n
                row["message"] = tmsg
                out_rows.append(row)
            op.write_text(json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8")
            filled += 1
    return filled


def coalesce_chunks(items: list[dict], chunks_in: Path, max_lines: int, max_chars: int) -> list[list[dict]]:
    if max_lines <= 0:
        return [[it] for it in items]
    groups: list[list[dict]] = []
    cur: list[dict] = []
    cur_lines = 0
    cur_chars = 0
    for it in items:
        rows = json.loads((chunks_in / it["chunk"]).read_text(encoding="utf-8"))
        cnt = len(rows)
        chars = sum(len(r.get("message", "")) for r in rows)
        if cur and (cur_lines + cnt > max_lines or cur_chars + chars > max_chars):
            groups.append(cur)
            cur = [it]
            cur_lines = cnt
            cur_chars = chars
        else:
            cur.append(it)
            cur_lines += cnt
            cur_chars += chars
    if cur:
        groups.append(cur)
    return groups


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", help="Path to gt_project or game root")
    parser.add_argument("--keys-file", help="Path to file containing one API key per line (or JSON)")
    parser.add_argument("--state-file", help="Optional path to shared key_quota_state.json")
    parser.add_argument("--reserve-rpd", type=int, help="Keep N daily requests unused per key")
    parser.add_argument("--plan-only", action="store_true", help="Print per-key quota allocation plan and exit without API calls")
    parser.add_argument("--model", default="gemini-4-hydrogen")
    parser.add_argument("--thinking-level", default="HIGH")
    parser.add_argument("--batch-size", type=int, default=18, help="Max API requests this wave (0 = drain available pool RPD)")
    parser.add_argument("--pack-lines", type=int, default=240, help="Coalesce consecutive small chunks up to N lines per request (0 = disable)")
    parser.add_argument("--pack-chars", type=int, default=16000, help="Max total Japanese chars per coalesced request")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--chunks", nargs="*")
    args = parser.parse_args()

    gp = resolve_gt_project(args.project)
    keys = init_keys(
        gp,
        args.keys_file,
        state_file=args.state_file,
        model=args.model,
        reserve_rpd=args.reserve_rpd,
    )
    mgr = get_quota_manager()
    chunks_in = gp / "chunks_in"
    chunks_out = gp / "chunks_out"
    manifest_path = gp / "manifest.json"
    skipped_path = gp / "skipped_chunks.json"

    name_map, glossary = load_glossary(gp)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_chunk = {m["chunk"]: m for m in manifest}
    skipped = set(json.loads(skipped_path.read_text(encoding="utf-8"))) if skipped_path.exists() else set()

    tm_pre = prefill_from_tm(manifest, chunks_in, chunks_out, name_map)
    if tm_pre:
        print(f"TM pre-fill auto-completed {tm_pre} duplicate chunks at 0 API cost.")

    if args.chunks:
        todo = [by_chunk[c] for c in args.chunks if c in by_chunk]
        groups = coalesce_chunks(todo, chunks_in, 0 if len(todo) == 1 else args.pack_lines, args.pack_chars)
    else:
        todo = [m for m in manifest if not (chunks_out / m["chunk"]).exists() and m["chunk"] not in skipped]
        all_groups = coalesce_chunks(todo, chunks_in, args.pack_lines, args.pack_chars)
        req_budget = mgr.total_remaining_rpd(args.model) if args.batch_size <= 0 else args.batch_size
        groups = all_groups[: max(0, req_budget)]

    if args.plan_only:
        plan = mgr.plan_workload(len(groups), model=args.model, chunk_names=[g[0]["chunk"] for g in groups])
        print(kqm.format_status_report(mgr, args.model))
        print(
            f"\nBatch Plan: packed_requests={len(groups)} (covering {sum(len(g) for g in groups)} chunks), "
            f"available={plan['total_available_rpd']} RPD, can_complete_today={plan['can_complete_today']}"
        )
        for a in plan["allocations"]:
            span = f" -> {a['chunks'][0]}..{a['chunks'][-1]}" if a["chunks"] else ""
            print(f"  [#{a['index']} {a['label']} ({a['masked']})] alloc={a['allocated']}/{a['remaining_rpd']}{span}")
        return

    if not groups:
        print("No chunks left to translate (or 0 remaining RPD in pool).")
        print(kqm.format_status_report(mgr, args.model))
        return

    total_target_chunks = sum(len(g) for g in groups)
    print(
        f"Translating {total_target_chunks} chunks via {len(groups)} smart request(s) "
        f"(pack<={args.pack_lines}L, model={args.model}/{args.thinking_level}, "
        f"keys={len(keys)}, remaining_pool_rpd={mgr.total_remaining_rpd(args.model)}, concurrency={args.concurrency})..."
    )
    t0 = time.monotonic()
    newly_refused = []
    quota_429 = 0
    ok_cnt = 0
    err_cnt = 0

    def worker(group: list[dict]) -> list[dict]:
        cnames = [g["chunk"] for g in group]
        label = cnames[0] if len(cnames) == 1 else f"{cnames[0]}..{cnames[-1]}({len(cnames)})"
        src_file = group[0]["source_file"] if len({g["source_file"] for g in group}) == 1 else "multi"
        orig_parts = [json.loads((chunks_in / c).read_text(encoding="utf-8")) for c in cnames]
        orig = [row for part in orig_parts for row in part]
        try:
            cleaned = translate_lines(args.model, label, src_file, orig, name_map, glossary, thinking_level=args.thinking_level)
            cursor = 0
            out = []
            for g, part in zip(group, orig_parts):
                sl = cleaned[cursor : cursor + len(part)]
                cursor += len(part)
                (chunks_out / g["chunk"]).write_text(json.dumps(sl, ensure_ascii=False, indent=2), encoding="utf-8")
                out.append({"chunk": g["chunk"], "source": g["source_file"], "ok": True, "lines": len(sl), "sample": sl[0]["message"][:36]})
            return out
        except Exception as exc:
            err = str(exc)
            is_refused = err.startswith("safety_") or "empty_items_refused" in err
            return [{"chunk": g["chunk"], "source": g["source_file"], "ok": False, "refused": is_refused, "error": err[:900]} for g in group]

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        nested_results = list(pool.map(worker, groups))
    results = [r for sub in nested_results for r in sub]

    elapsed = max(0.1, time.monotonic() - t0)
    lines_done = 0
    for r in results:
        if r["ok"]:
            ok_cnt += 1
            lines_done += r["lines"]
            print(f"  [OK]   {r['chunk']} ({r['source']}): {r['lines']} lines | {r['sample']!r}")
        elif r.get("refused"):
            newly_refused.append(r["chunk"])
            print(f"  [SKIP] {r['chunk']} ({r['source']}): {r['error']}")
        else:
            err_cnt += 1
            if str(r.get("error", "")).startswith("quota_429"):
                quota_429 += 1
            print(f"  [ERR]  {r['chunk']} ({r['source']}): {r['error']}")

    if newly_refused:
        merged_skip = sorted(skipped | set(newly_refused))
        skipped_path.write_text(json.dumps(merged_skip, ensure_ascii=False, indent=2), encoding="utf-8")

    tm_post = prefill_from_tm(manifest, chunks_in, chunks_out, name_map)
    if tm_post:
        print(f"Post-wave TM auto-completed {tm_post} duplicate chunks at 0 API cost.")

    out_chunks = sorted(chunks_out.glob("chunk_*.json"))
    total_lines = sum(int(m["count"]) for m in manifest)
    total_done = sum(len(json.loads(c.read_text(encoding="utf-8"))) for c in out_chunks)
    print(f"\nWave finished in {elapsed:.1f}s ({lines_done / elapsed:.2f} lines/s): ok={ok_cnt}/{total_target_chunks}, refused={len(newly_refused)}, errors={err_cnt}, quota_429={quota_429}")
    print(kqm.format_status_report(mgr, args.model))
    print(f"Progress: chunks={len(out_chunks)}/{len(manifest)}, lines={total_done}/{total_lines} ({total_done / total_lines * 100:.2f}%)")


if __name__ == "__main__":
    main()
