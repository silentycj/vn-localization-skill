"""Wave-based parallel Visual Novel script translation workflow (model=inherit + humanizer-zh + adaptive usage control)."""

import json
import time
from pathlib import Path


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
    batch = todo[offset : offset + batch_size]

model_tier = str(args.get("model", "inherit"))

log(
    f"Running wave count={len(batch)} batch_size={batch_size} concurrency={cur_conc} "
    f"model={model_tier} skipped={len(skipped)} dir={GP}"
)
phase(f"Translate {len(batch)} chunks ({model_tier} + humanizer-zh)")

newly_refused = []


async def translate_one(item):
    cname = item["chunk"]
    src_file = item["source_file"]
    orig = json.load(open(CHUNKS_IN / cname, "r", encoding="utf-8"))
    numbered = []
    for i, entry in enumerate(orig):
        rec = {"idx": i, "message": entry["message"]}
        if "name" in entry:
            rec["name"] = entry["name"]
        numbered.append(rec)

    payload_text = json.dumps(numbered, ensure_ascii=False, indent=1)
    glossary_block = f"{GLOSSARY}\n\n" if GLOSSARY else ""
    base_prompt = (
        f"Translate Visual Novel script chunk {cname} (source {src_file}, {len(orig)} lines) "
        "from Japanese to natural, idiomatic Simplified Chinese following humanizer-zh standards:\n"
        "1. Preserve ALL original facts, tone, emotion, agency, negatives and nuances—never omit, merge, summarize, or invent details.\n"
        "2. Avoid stiff translationese / AI-style phrasing: avoid stacked 「的」, excessive 「进行+动词」, unnecessary passive 「被」, or repetitive 「她/他」 sentence openings; use natural active verbs and authentic spoken Chinese dialogue matching each character's personality.\n"
        "3. Keep dialogue quotes 「...」, inner thoughts （...）, ellipsis …… and dashes —— consistent with Simplified Chinese VN convention. Preserve any actual \\r\\n line breaks without escaping them as literal \\\\r\\\\n. Never leave Japanese hiragana or katakana (such as ぎ, ぎゃ, ぬ, ええ, っ) inside screams or sound effects—always render all cries and sfx into natural Chinese characters.\n"
        "4. Strictly follow all fixed translations (Characters/人物, Items/道具, Locations/地点, Terms/专有名词) in the glossary below with zero variation. Keep all engine control tags/variables verbatim (such as [r], [l], [p], {w}, %s, %d, \\V[n], \\C[n], <...>), and either keep [reading/base] ruby markers intact for CJK bases or omit furigana cleanly without leaving stray brackets.\n"
        "5. Return via finish with `items` containing EXACTLY "
        f"{len(orig)} objects with matching `idx` (0..{len(orig)-1}). Include `name` ONLY on entries that originally have `name`.\n"
        f"{glossary_block}"
        f"INPUT JSON:\n{payload_text}"
    )

    last_err = None
    for attempt in range(2):
        try:
            prompt = (
                base_prompt
                if attempt == 0
                else (
                    f"{base_prompt}\n\nPREVIOUS ATTEMPT FAILED ({last_err}). "
                    f"Ensure `items` has EXACTLY {len(orig)} items indexed 0..{len(orig)-1} with non-empty `message`."
                )
            )
            res = await agent(prompt, schema=SCHEMA, role=f"TL {cname} ({src_file})", model=model_tier)
            got = res.get("items", [])
            if len(got) == 0:
                newly_refused.append(cname)
                return {"chunk": cname, "source": src_file, "ok": False, "refused": True, "error": "empty_items_refused"}
            if len(got) == 1 and len(orig) > 3:
                msg0 = str(got[0].get("message", ""))
                if "Refus" in msg0 or "Cannot" in msg0 or "explicit" in msg0:
                    newly_refused.append(cname)
                    return {"chunk": cname, "source": src_file, "ok": False, "refused": True, "error": msg0[:120]}
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
                    msg = str(dst_e.get("message", "")).strip().replace("\\r\\n", "\r\n")
                    if not msg:
                        raise ValueError(f"empty_msg_at_{i}")
                out_e = {}
                if "name" in src_e:
                    raw_n = src_e["name"]
                    out_e["name"] = NAME_MAP.get(raw_n) or (str(dst_e.get("name", "")).strip() or raw_n)
                out_e["message"] = msg
                cleaned.append(out_e)

            with open(CHUNKS_OUT / cname, "w", encoding="utf-8") as f:
                json.dump(cleaned, f, ensure_ascii=False, indent=2)
            return {"chunk": cname, "source": src_file, "ok": True, "lines": len(cleaned)}
        except Exception as exc:
            last_err = str(exc)
            if "content safety filters" in last_err or "Prohibited Use policy" in last_err:
                newly_refused.append(cname)
                return {"chunk": cname, "source": src_file, "ok": False, "refused": True, "error": "content_safety_filter"}
            if "ended without calling finish" in last_err:
                newly_refused.append(cname)
                return {"chunk": cname, "source": src_file, "ok": False, "refused": True, "error": "ended_without_finish_refused"}
            if "RESOURCE_EXHAUSTED" in last_err or "429" in last_err:
                return {"chunk": cname, "source": src_file, "ok": False, "refused": False, "error": f"quota_429: {last_err[:120]}"}
    return {"chunk": cname, "source": src_file, "ok": False, "refused": False, "error": (last_err or "unknown")[:160]}


t0 = time.monotonic()
results = []
pos = 0
quota_halted = False
while pos < len(batch):
    sub = batch[pos : pos + cur_conc]
    pos += len(sub)
    sub_res = await parallel(sub, translate_one)
    results.extend(sub_res)
    sub_429 = sum(1 for r in sub_res if str(r.get("error", "")).startswith("quota_429"))
    sub_ok = sum(1 for r in sub_res if r.get("ok"))
    if sub_429 > 0:
        cur_conc = max(2, cur_conc // 2)
        log(f"Quota 429 detected in window ({sub_429}/{len(sub)}); backing off concurrency -> {cur_conc}")
        if sub_ok == 0:
            quota_halted = True
            log("All requests in window hit 429; halting wave early to preserve quota.")
            break

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
