"""Layered slice-down retry workflow for skipped_chunks.json or safety_blocked_segments.json."""

import json
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
BLOCKED_JSON = GP / "safety_blocked_segments.json"
BLOCKED_TXT = GP / "safety_blocked_segments.txt"

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

# mode: "blocked" retries safety_blocked_segments.json; "skipped" splits full chunks listed in args["chunks"]
mode = str(args.get("mode", "blocked"))
target_chunks = set(args.get("chunks", []))
sub_size = int(args.get("sub_size", 10 if mode == "blocked" else 20))
model_tier = str(args.get("model", "inherit"))

all_blocked = json.load(open(BLOCKED_JSON, "r", encoding="utf-8")) if BLOCKED_JSON.exists() else []
loaded_orig = {}
sub_tasks = []
selected_keys = set()

if mode == "skipped":
    cnames = [c for c in args.get("chunks", []) if c in by_chunk]
    for cname in cnames:
        orig = json.load(open(CHUNKS_IN / cname, "r", encoding="utf-8"))
        loaded_orig[cname] = orig
        for s in range(0, len(orig), sub_size):
            e = min(len(orig), s + sub_size)
            sub_tasks.append((cname, s, e))
else:
    selected_segments = [
        b for b in all_blocked
        if (not target_chunks or b["chunk"] in target_chunks) and b["chunk"] in by_chunk
    ]
    for b in selected_segments:
        cname = b["chunk"]
        if cname not in loaded_orig:
            loaded_orig[cname] = json.load(open(CHUNKS_IN / cname, "r", encoding="utf-8"))
        ls, le = b["chunk_local_range"]
        selected_keys.add((cname, (ls, le)))
        for s in range(ls, le, sub_size):
            e = min(le, s + sub_size)
            sub_tasks.append((cname, s, e))

log(f"Retry mode={mode}: {len(loaded_orig)} chunks, {len(sub_tasks)} slices (sub_size={sub_size}, model={model_tier})")
phase(f"Retry {len(sub_tasks)} slices ({model_tier} + humanizer-zh)")


async def run_slice(task):
    cname, s, e = task
    meta = by_chunk[cname]
    src_file = meta["source_file"]
    orig = loaded_orig[cname]
    sl = orig[s:e]
    numbered = []
    for i, entry in enumerate(sl):
        rec = {"idx": i, "message": entry["message"]}
        if "name" in entry:
            rec["name"] = entry["name"]
        numbered.append(rec)

    payload_text = json.dumps(numbered, ensure_ascii=False, indent=1)
    glossary_block = f"{GLOSSARY}\n\n" if GLOSSARY else ""
    prompt = (
        f"Translate Visual Novel slice {cname}[{s}:{e}] (source {src_file}, {len(sl)} lines) "
        "from Japanese to natural, idiomatic Simplified Chinese following humanizer-zh standards:\n"
        "1. Preserve ALL original facts, tone, emotion, agency, negatives and nuances—never omit, merge, summarize, or invent details.\n"
        "2. Avoid stiff translationese / AI-style phrasing: avoid stacked 「的」, excessive 「进行+动词」, unnecessary passive 「被」, or repetitive 「她/他」 sentence openings; use natural active verbs and authentic spoken Chinese dialogue matching each character's personality.\n"
        "3. Keep dialogue quotes 「...」, inner thoughts （...）, ellipsis …… and dashes —— consistent with Simplified Chinese VN convention. Preserve any actual \\r\\n line breaks without escaping them as literal \\\\r\\\\n. Never leave Japanese hiragana or katakana (such as ぎ, ぎゃ, ぬ, ええ, っ) inside screams or sound effects—always render all cries and sfx into natural Chinese characters.\n"
        "4. Strictly follow all fixed translations (Characters/人物, Items/道具, Locations/地点, Terms/专有名词) in the glossary below with zero variation. Keep all engine control tags/variables verbatim (such as [r], [l], [p], {w}, %s, %d, \\V[n], \\C[n], <...>), and either keep [reading/base] ruby markers intact for CJK bases or omit furigana cleanly without leaving stray brackets.\n"
        "5. Return via finish with `items` containing EXACTLY "
        f"{len(sl)} objects with matching `idx` (0..{len(sl)-1}). Include `name` ONLY on entries that originally have `name`.\n"
        f"{glossary_block}"
        f"INPUT JSON:\n{payload_text}"
    )

    try:
        res = await agent(prompt, schema=SCHEMA, role=f"Retry {cname}[{s}:{e}]", model=model_tier)
        got = res.get("items", [])
        if len(got) == 0:
            return {"chunk": cname, "s": s, "e": e, "ok": False, "reason": "empty_items_refused"}
        if len(got) == 1 and len(sl) > 1:
            msg0 = str(got[0].get("message", ""))
            if "Refus" in msg0 or "Cannot" in msg0 or "explicit" in msg0:
                return {"chunk": cname, "s": s, "e": e, "ok": False, "reason": f"model_refused: {msg0[:120]}"}
        if len(got) != len(sl):
            by_idx = {int(x["idx"]): x for x in got if isinstance(x, dict) and "idx" in x}
            if len(by_idx) == len(sl) and all(i in by_idx for i in range(len(sl))):
                got = [by_idx[i] for i in range(len(sl))]
            else:
                return {"chunk": cname, "s": s, "e": e, "ok": False, "reason": f"count_mismatch_{len(got)}_{len(sl)}"}

        cleaned = []
        for i, (src_e, dst_e) in enumerate(zip(sl, got)):
            raw_src = src_e["message"]
            if not raw_src.strip():
                msg = raw_src
            else:
                msg = str(dst_e.get("message", "")).strip().replace("\\r\\n", "\r\n")
                if not msg:
                    return {"chunk": cname, "s": s, "e": e, "ok": False, "reason": f"empty_msg_at_{i}"}
            out_e = {}
            if "name" in src_e:
                raw_n = src_e["name"]
                out_e["name"] = NAME_MAP.get(raw_n) or (str(dst_e.get("name", "")).strip() or raw_n)
            out_e["message"] = msg
            cleaned.append(out_e)
        return {"chunk": cname, "s": s, "e": e, "ok": True, "entries": cleaned}
    except Exception as exc:
        err = str(exc)
        if "content safety filters" in err or "Prohibited Use policy" in err:
            reason = "content_safety_filter"
        elif "ended without calling finish" in err:
            reason = "ended_without_finish_refused"
        elif "RESOURCE_EXHAUSTED" in err or "429" in err:
            reason = "quota_429: " + err[:120]
        else:
            reason = err[:160]
        return {"chunk": cname, "s": s, "e": e, "ok": False, "reason": reason}


usage_path = GP / "usage_state.json"
default_slice_conc = 20
if usage_path.exists():
    try:
        ust = json.load(open(usage_path, "r", encoding="utf-8"))
        default_slice_conc = max(3, min(20, int(ust.get("concurrency", 12)) * 2))
    except Exception:
        pass
slice_conc = max(2, int(args.get("concurrency", default_slice_conc)))

slice_results = []
pos = 0
retry_429_cnt = 0
quota_halted = False
while pos < len(sub_tasks):
    sub = sub_tasks[pos : pos + slice_conc]
    pos += len(sub)
    sub_res = await parallel(sub, run_slice)
    sub_429 = [r for r in sub_res if str(r.get("reason", "")).startswith("quota_429")]
    retry_429_cnt += len(sub_429)
    # Do not overwrite chunks with Japanese fallback for quota_429 transient failures
    non_429 = [r for r in sub_res if not str(r.get("reason", "")).startswith("quota_429")]
    slice_results.extend(non_429)
    if sub_429:
        slice_conc = max(2, slice_conc // 2)
        log(f"Quota 429 during retry ({len(sub_429)}/{len(sub)}); backing off concurrency -> {slice_conc}")
        if len(sub_429) == len(sub):
            quota_halted = True
            log(
                "QUOTA_INSUFFICIENT: 当前模型可用额度不足，重试已提前安全中止（未执行切片保持原状）。"
                "请告知用户等待配额恢复后继续。"
            )
            break

by_c_res = {}
for r in slice_results:
    by_c_res.setdefault(r["chunk"], []).append(r)

if mode == "skipped":
    processed_chunks = set(loaded_orig.keys())
    remaining_blocked = [b for b in all_blocked if b.get("chunk") not in processed_chunks]
else:
    remaining_blocked = [
        b for b in all_blocked
        if (b["chunk"], tuple(b["chunk_local_range"])) not in selected_keys
    ]

recovered_lines = 0
still_blocked_lines = 0
chunks_written = 0

for cname, orig in loaded_orig.items():
    meta = by_chunk[cname]
    src_file = meta["source_file"]
    file_base = int(meta["start"])
    out_path = CHUNKS_OUT / cname
    merged = json.load(open(out_path, "r", encoding="utf-8")) if out_path.exists() else list(orig)

    ok_slices = 0
    for r in sorted(by_c_res.get(cname, []), key=lambda x: x["s"]):
        s, e = r["s"], r["e"]
        if r["ok"]:
            for off, entry in enumerate(r["entries"]):
                merged[s + off] = entry
            ok_slices += 1
            recovered_lines += (e - s)
        else:
            for off in range(e - s):
                merged[s + off] = orig[s + off]
            still_blocked_lines += (e - s)
            remaining_blocked.append({
                "chunk": cname,
                "source_file": src_file,
                "chunk_local_range": [s, e],
                "file_entry_range": [file_base + s, file_base + e],
                "line_count": e - s,
                "reason": r["reason"],
                "sample_first": orig[s]["message"],
                "sample_last": orig[e - 1]["message"],
            })

    if ok_slices > 0 or (mode == "skipped" and by_c_res.get(cname)):
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        chunks_written += 1

remaining_blocked.sort(key=lambda x: (x["chunk"], x["chunk_local_range"][0]))
with open(BLOCKED_JSON, "w", encoding="utf-8") as f:
    json.dump(remaining_blocked, f, ensure_ascii=False, indent=2)

with open(BLOCKED_TXT, "w", encoding="utf-8") as f:
    f.write("以下段落因触发内容安全拦截或模型拒翻而保留日语原文（供人工对照）：\n")
    f.write("=" * 72 + "\n")
    for b in remaining_blocked:
        ls, le = b["chunk_local_range"]
        fs, fe = b["file_entry_range"]
        f.write(
            f"[{b['source_file']}] {b['chunk']} 局部[{ls}:{le}] 文件行号[{fs}:{fe}] "
            f"({b['line_count']}行) | 原因: {b['reason']}\n"
            f"  首句: {b['sample_first']}\n"
            f"  末句: {b['sample_last']}\n\n"
        )

log(
    f"Retry complete: chunks_updated={chunks_written}/{len(loaded_orig)}, "
    f"recovered_lines={recovered_lines}, still_blocked_lines={still_blocked_lines}, "
    f"total_remaining_segments={len(remaining_blocked)}"
)
out_chunks = sorted(CHUNKS_OUT.glob("chunk_*.json"))
done_lines = sum(len(json.load(open(c, "r", encoding="utf-8"))) for c in out_chunks)
total_lines = sum(int(m["count"]) for m in manifest)
log(f"Progress: chunks={len(out_chunks)}/{len(manifest)}, lines={done_lines}/{total_lines} ({done_lines / total_lines * 100:.2f}%)" if total_lines else f"Progress: chunks={len(out_chunks)}/{len(manifest)}")
