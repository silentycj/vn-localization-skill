"""Wave-based parallel Visual Novel script translation workflow (model=inherit + humanizer-zh + adaptive usage control)."""

import json
import re
import time
from pathlib import Path

REFUSAL_RE = re.compile(
    r"Unable to fulfill|Cannot (?:translate|polish|fulfill|comply)|I cannot|Refus(?:ed|al)|violate|Prohibited Use|explicit sexual",
    re.I,
)


def resolve_project_dir() -> Path:
    raw = args.get("project_dir")
    if raw:
        return Path(raw)
    roots = [Path(w) for w in WORKSPACES] if "WORKSPACES" in globals() and WORKSPACES else [Path.cwd()]
    for ws in roots:
        direct = ws / "work" / "gt_project"
        if (direct / "manifest.json").exists():
            return direct
        for cand in sorted(ws.glob("*/work/gt_project")):
            if (cand / "manifest.json").exists():
                return cand
    raise FileNotFoundError("Could not locate gt_project/manifest.json; pass args['project_dir'].")


GP = resolve_project_dir()
CHUNKS_IN = GP / "chunks_in"
CHUNKS_OUT = GP / "chunks_out"
MANIFEST = GP / "manifest.json"
SKIPPED_PATH = GP / "skipped_chunks.json"
USAGE_STATE_PATH = GP / "usage_state.json"

glossary_file = Path(args["glossary_path"]) if args.get("glossary_path") else (GP / "glossary.json")
NAME_MAP = {}
GLOSSARY = ""
if glossary_file.exists():
    gdata = json.load(open(glossary_file, "r", encoding="utf-8"))
    NAME_MAP = dict(gdata.get("name_map", {}))
    GLOSSARY = str(gdata.get("glossary", "")).strip()

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "name": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["idx", "message"],
            },
        }
    },
    "required": ["items"],
}

CHUNKS_OUT.mkdir(parents=True, exist_ok=True)
manifest = json.load(open(MANIFEST, "r", encoding="utf-8"))
by_chunk = {it["chunk"]: it for it in manifest}

skipped = set()
if SKIPPED_PATH.exists():
    try:
        skipped.update(json.load(open(SKIPPED_PATH, "r", encoding="utf-8")))
    except Exception:
        pass

usage_state = {}
if USAGE_STATE_PATH.exists():
    try:
        usage_state = json.load(open(USAGE_STATE_PATH, "r", encoding="utf-8"))
    except Exception:
        usage_state = {}

raw_batch_arg = args.get("batch_size")
if raw_batch_arg in (None, 0, "auto"):
    batch_size = int(usage_state.get("recommended_batch_size", 12))
else:
    batch_size = int(raw_batch_arg)
default_conc = int(usage_state.get("concurrency", min(batch_size, 12)))
cur_conc = max(2, min(15, int(args.get("concurrency", default_conc))))

max_chunks_arg = args.get("max_chunks")
if max_chunks_arg in ("all", 0) or args.get("auto_continue"):
    target_total_chunks = len(manifest)
elif max_chunks_arg is not None:
    target_total_chunks = int(max_chunks_arg)
else:
    target_total_chunks = batch_size
coalesce_max_lines = int(args.get("coalesce_max_lines", 240))
coalesce_max_chars = int(args.get("coalesce_max_chars", 16000))

# Zero-cost inline TM pre-fill from already-translated chunks_out
def prefill_from_tm() -> int:
    tm = {}
    for m in manifest:
        op = CHUNKS_OUT / m["chunk"]
        if not op.exists():
            continue
        sr = json.load(open(CHUNKS_IN / m["chunk"], "r", encoding="utf-8"))
        dr = json.load(open(op, "r", encoding="utf-8"))
        for s_ent, d_ent in zip(sr, dr):
            if d_ent.get("message") != s_ent.get("message"):
                tm.setdefault((s_ent.get("name", ""), s_ent["message"]), (d_ent.get("name"), d_ent["message"]))
    if not tm:
        return 0
    filled = 0
    for m in manifest:
        op = CHUNKS_OUT / m["chunk"]
        if op.exists():
            continue
        sr = json.load(open(CHUNKS_IN / m["chunk"], "r", encoding="utf-8"))
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
                    row["name"] = NAME_MAP.get(raw_n) or tn or raw_n
                row["message"] = tmsg
                out_rows.append(row)
            with open(op, "w", encoding="utf-8") as f:
                json.dump(out_rows, f, ensure_ascii=False, indent=2)
            filled += 1
    return filled


tm_filled = prefill_from_tm()
if tm_filled:
    log(f"TM pre-fill auto-completed {tm_filled} duplicate chunks at 0 API cost")

explicit_chunks = args.get("chunks")
allow_overwrite = bool(args.get("overwrite", False))
if explicit_chunks:
    batch = [
        by_chunk[c]
        for c in explicit_chunks
        if c in by_chunk and (allow_overwrite or not (CHUNKS_OUT / c).exists())
    ]
else:
    todo = [
        it
        for it in manifest
        if (allow_overwrite or not (CHUNKS_OUT / it["chunk"]).exists()) and it["chunk"] not in skipped
    ]
    offset = int(args.get("offset", 0))
    batch = todo[offset : offset + target_total_chunks]

model_tier = str(args.get("model", "inherit"))


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


def build_prompt(label: str, src_file: str, orig: list[dict], retry_note: str = "") -> str:
    payload_text = encode_compact_payload(orig)
    filtered_glossary = filter_glossary_for_batch(orig, GLOSSARY, NAME_MAP)
    glossary_block = f"GLOSSARY:\n{filtered_glossary}\n\n" if filtered_glossary else ""
    base_prompt = (
        f"Translate VN {label} ({src_file}, {len(orig)} lines) JP->Simplified Chinese (humanizer-zh):\n"
        "1. Keep ALL facts, tone, emotion, agency, negatives & nuances verbatim; no omissions/merges/inventions.\n"
        "2. Natural spoken Chinese matching speaker voice; avoid stacked 「的」, 「进行+V」, stiff 「被」, repetitive 「她/他」.\n"
        "3. Sensual, Anatomy & Organ-Action Fidelity: Keep full erotic tension, shame-arousal contrast, organ terms & physical organ actions vivid (avoid medical/textbook terms `粘膜/突起/分泌物/私处/子袋` or flat verbs `触碰/进入/刺激`). Match voice: heroine (`蜜穴/花瓣/花壁/花核/乳尖/子宫口/后庭`) vs antagonist (`淫穴/骚屄/肉棒/龟头/屁眼/奶头`) with visceral actions (`抠挖/揉弄/舔舐/吮吸/挺腰贯入/深抽浅送/抵住花心猛撞/痉挛紧咬/涌出淫水/喷洒精液`).\n"
        "4. Natural Chinese SFX/cries: Convert kana SFX into idiomatic Chinese sound/cadence (not ザアァ->沙啊啊, あぐっ->啊咕, ぎゃっ->啊嘎, ひぃっ->哈咿, or long stacks like 咕滋滋滋滋); crisp stutters; prefer …… / —— over ～～; zero leftover kana.\n"
        "5. Keep 「...」, （...）, ……, ——, tags ([r],[l],[p],{w},%s,%d,\\V[n],<...>) & \\r\\n breaks; strip [・・・/词] or [reading/base] ruby brackets to plain Chinese.\n"
        f"6. Input format is `idx\\tspeaker\\tmessage` ({len(orig)} lines). Return via finish `items` with EXACTLY {len(orig)} objects `{{idx, message}}` (0..{len(orig)-1}); omit `name` unless speaker is unmapped.\n"
        f"{glossary_block}"
        f"INPUT:\n{payload_text}"
    )
    return base_prompt if not retry_note else f"{base_prompt}\n\nRETRY: {retry_note}"


def coalesce_items(items: list[dict], max_lines: int, max_chars: int = 16000) -> list[list[dict]]:
    if max_lines <= 0:
        return [[it] for it in items]
    groups: list[list[dict]] = []
    cur: list[dict] = []
    cur_lines = 0
    cur_chars = 0
    for it in items:
        cnt = int(it["count"])
        ch = int(it.get("chars", cnt * 24))
        if cur and (cur_lines + cnt > max_lines or cur_chars + ch > max_chars):
            groups.append(cur)
            cur = [it]
            cur_lines = cnt
            cur_chars = ch
        else:
            cur.append(it)
            cur_lines += cnt
            cur_chars += ch
    if cur:
        groups.append(cur)
    return groups


packed_groups = coalesce_items(batch, coalesce_max_lines, coalesce_max_chars)
log(
    f"Running wave chunks={len(batch)} -> {len(packed_groups)} packed_calls (coalesce<={coalesce_max_lines}L/{coalesce_max_chars}C) "
    f"window={batch_size} concurrency={cur_conc} model={model_tier} skipped={len(skipped)} dir={GP}"
)
phase(f"Translate {len(batch)} chunks in {len(packed_groups)} smart calls ({model_tier} + humanizer-zh)")

newly_refused = []


async def translate_group(group: list[dict]):
    cnames = [g["chunk"] for g in group]
    label = cnames[0] if len(cnames) == 1 else f"{cnames[0]}..{cnames[-1]}({len(cnames)})"
    src_file = group[0]["source_file"] if len({g["source_file"] for g in group}) == 1 else "multi"
    orig_parts = [json.load(open(CHUNKS_IN / c, "r", encoding="utf-8")) for c in cnames]
    orig = [row for part in orig_parts for row in part]

    last_err = None
    for attempt in range(2):
        try:
            retry_note = "" if attempt == 0 else f"PREVIOUS ATTEMPT FAILED ({last_err}). Ensure `items` has EXACTLY {len(orig)} items indexed 0..{len(orig)-1} with non-empty `message`."
            prompt = build_prompt(label, src_file, orig, retry_note=retry_note)
            res = await agent(prompt, schema=SCHEMA, role=f"TL {label} ({src_file})", model=model_tier)
            got = res.get("items", [])
            if len(got) == 0:
                newly_refused.extend(cnames)
                return [{"chunk": c, "source": src_file, "ok": False, "refused": True, "error": "empty_items_refused"} for c in cnames]
            if len(got) == 1 and len(orig) > 3:
                msg0 = str(got[0].get("message", ""))
                if "Refus" in msg0 or "Cannot" in msg0 or "explicit" in msg0:
                    newly_refused.extend(cnames)
                    return [{"chunk": c, "source": src_file, "ok": False, "refused": True, "error": msg0[:120]} for c in cnames]
            if len(got) != len(orig):
                by_idx = {int(x["idx"]): x for x in got if isinstance(x, dict) and "idx" in x}
                if len(by_idx) == len(orig) and all(i in by_idx for i in range(len(orig))):
                    got = [by_idx[i] for i in range(len(orig))]
                else:
                    raise ValueError(f"count_mismatch_{len(got)}_{len(orig)}")

            cleaned = []
            for i, (src_e, dst_e) in enumerate(zip(orig, got)):
                raw_src = src_e["message"]
                if not raw_src.strip():
                    msg = raw_src
                else:
                    msg = str(dst_e.get("message", "")).strip().replace("\\r\\n", "\r\n").replace("\\n", "\n").replace("\\t", "\t")
                    if not msg:
                        raise ValueError(f"empty_msg_at_{i}")
                    if REFUSAL_RE.search(msg):
                        newly_refused.extend(cnames)
                        return [{"chunk": g["chunk"], "source": g["source_file"], "ok": False, "refused": True, "error": f"model_refused: {msg[:100]}"} for g in group]
                out_e = {}
                if "name" in src_e:
                    raw_n = src_e["name"]
                    out_e["name"] = NAME_MAP.get(raw_n) or (str(dst_e.get("name", "")).strip() or raw_n)
                out_e["message"] = msg
                cleaned.append(out_e)

            cursor = 0
            out_res = []
            for g, part in zip(group, orig_parts):
                sl_out = cleaned[cursor : cursor + len(part)]
                cursor += len(part)
                with open(CHUNKS_OUT / g["chunk"], "w", encoding="utf-8") as f:
                    json.dump(sl_out, f, ensure_ascii=False, indent=2)
                out_res.append({"chunk": g["chunk"], "source": g["source_file"], "ok": True, "lines": len(sl_out)})
            return out_res
        except Exception as exc:
            last_err = str(exc)
            if "content safety filters" in last_err or "Prohibited Use policy" in last_err:
                newly_refused.extend(cnames)
                return [{"chunk": g["chunk"], "source": g["source_file"], "ok": False, "refused": True, "error": "content_safety_filter"} for g in group]
            if "ended without calling finish" in last_err:
                newly_refused.extend(cnames)
                return [{"chunk": g["chunk"], "source": g["source_file"], "ok": False, "refused": True, "error": "ended_without_finish_refused"} for g in group]
            if "RESOURCE_EXHAUSTED" in last_err or "429" in last_err:
                return [{"chunk": g["chunk"], "source": g["source_file"], "ok": False, "refused": False, "error": f"quota_429: {last_err[:120]}"} for g in group]
    return [{"chunk": g["chunk"], "source": g["source_file"], "ok": False, "refused": False, "error": (last_err or "unknown")[:160]} for g in group]


t0 = time.monotonic()
results = []
pos = 0
quota_halted = False
while pos < len(packed_groups):
    sub = packed_groups[pos : pos + cur_conc]
    pos += len(sub)
    sub_nested = await parallel(sub, translate_group)
    sub_res = [r for grp in sub_nested for r in grp]
    results.extend(sub_res)
    sub_429 = sum(1 for r in sub_res if str(r.get("error", "")).startswith("quota_429"))
    sub_ok = sum(1 for r in sub_res if r.get("ok"))
    if sub_429 > 0:
        cur_conc = max(2, cur_conc // 2)
        log(f"Quota 429 detected in window ({sub_429}); backing off concurrency -> {cur_conc}")
        if sub_ok == 0:
            quota_halted = True
            log("All requests in window hit 429; halting wave early to preserve quota.")
            break
    elif len(sub) == cur_conc and cur_conc < 15:
        cur_conc = min(15, cur_conc + 1)

# Re-run TM pre-fill once more after all waves in case later Recollection chunks match earlier waves!
tm_post = prefill_from_tm()
if tm_post:
    log(f"Post-wave TM auto-completed {tm_post} duplicate chunks at 0 API cost")

elapsed = max(0.1, time.monotonic() - t0)

if newly_refused:
    merged_skip = sorted(skipped | set(newly_refused))
    with open(SKIPPED_PATH, "w", encoding="utf-8") as f:
        json.dump(merged_skip, f, ensure_ascii=False, indent=2)

ok_cnt = sum(1 for r in results if r["ok"])
refused_cnt = sum(1 for r in results if r.get("refused"))
quota_429_cnt = sum(1 for r in results if str(r.get("error", "")).startswith("quota_429"))
err_cnt = len(results) - ok_cnt - refused_cnt
lines_done_wave = sum(int(r.get("lines", 0)) for r in results if r.get("ok"))
lps = lines_done_wave / elapsed

clean_waves = int(usage_state.get("consecutive_clean_waves", 0))
if quota_429_cnt > 0:
    next_conc = max(2, min(cur_conc, 4 if quota_halted else 6))
    next_batch = max(2, min(next_conc * 2, max(2, batch_size // 2)))
    clean_waves = 0
elif err_cnt == 0 and ok_cnt > 0:
    clean_waves += 1
    if lps >= 12.0 and clean_waves >= 2:
        next_conc = min(15, cur_conc + 2)
        next_batch = min(15, max(batch_size, len(batch)) + 2)
    elif lps < 5.0:
        next_conc = max(4, min(cur_conc, 8))
        next_batch = max(6, min(10, len(batch)))
    else:
        next_conc = cur_conc
        next_batch = max(6, min(15, len(batch) if len(batch) >= 6 else batch_size))
else:
    clean_waves = 0
    next_conc = max(3, min(cur_conc, 8))
    next_batch = max(4, min(10, batch_size))

usage_out = {
    "recommended_batch_size": next_batch,
    "concurrency": next_conc,
    "consecutive_clean_waves": clean_waves,
    "last_elapsed_sec": round(elapsed, 2),
    "last_lines_per_sec": round(lps, 2),
    "last_quota_429": quota_429_cnt,
    "quota_insufficient": bool(quota_halted or (len(results) > 0 and quota_429_cnt > 0 and ok_cnt == 0)),
    "last_ok": ok_cnt,
    "last_attempted": len(results),
}
with open(USAGE_STATE_PATH, "w", encoding="utf-8") as f:
    json.dump(usage_out, f, ensure_ascii=False, indent=2)

log(f"Wave finished: ok={ok_cnt}/{len(results)}, skipped_refused={refused_cnt}, errors={err_cnt}, quota_429={quota_429_cnt}")
if usage_out["quota_insufficient"]:
    log(
        "QUOTA_INSUFFICIENT: 当前模型可用额度/并发配额不足以继续翻译，已提前安全中止本批后续分块并保留已完成进度。"
        "请告知用户等待配额恢复后再继续（已完成分块已保存，无需重跑）。"
    )
elif quota_429_cnt > 0:
    log(
        f"QUOTA_PRESSURE: 本批出现 {quota_429_cnt} 次 429 限流，已自动下调下一批建议规模为 "
        f"batch_size={next_batch}, concurrency={next_conc}；若可用量仍紧张请告知用户。"
    )
log(f"Usage stats: elapsed={elapsed:.1f}s, throughput={lps:.2f} lines/s | next recommended batch_size={next_batch}, concurrency={next_conc}")
for r in results:
    if not r["ok"]:
        log(f"  FAILED {r['chunk']}: {r['error']}")

out_chunks = sorted(CHUNKS_OUT.glob("chunk_*.json"))
total_chunks = len(manifest)
done_lines = sum(len(json.load(open(c, "r", encoding="utf-8"))) for c in out_chunks)
total_lines = sum(int(m["count"]) for m in manifest)
log(f"Progress: chunks={len(out_chunks)}/{total_chunks}, lines={done_lines}/{total_lines} ({done_lines / total_lines * 100:.2f}%)" if total_lines else f"Progress: chunks={len(out_chunks)}/{total_chunks}")
