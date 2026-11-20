"""Layered slice-down retry for refused chunks via Google AI Studio API.

Reuses translate_lines/init_keys/load_glossary from api_translate_wave.py.

Usage:
    python api_retry_blocked.py [--project <gt_project>] [--keys-file <api_keys.txt>] \
        --mode skipped --chunks chunk_0199.json --sub-size 20 --concurrency 3
    python api_retry_blocked.py --mode blocked --sub-size 10 --concurrency 3
"""

import argparse
import concurrent.futures
import json
from pathlib import Path

import api_translate_wave as apiw
import key_quota_manager as kqm


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", help="Path to gt_project or game root")
    parser.add_argument("--keys-file", help="Path to file containing one API key per line")
    parser.add_argument("--state-file", help="Optional path to shared key_quota_state.json")
    parser.add_argument("--reserve-rpd", type=int, help="Keep N daily requests unused per key")
    parser.add_argument("--mode", choices=["skipped", "blocked"], default="skipped")
    parser.add_argument("--model", default="gemini-4-hydrogen")
    parser.add_argument("--thinking-level", default="HIGH")
    parser.add_argument("--sub-size", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--chunks", nargs="*")
    args = parser.parse_args()

    gp = apiw.resolve_gt_project(args.project)
    apiw.init_keys(gp, args.keys_file, state_file=args.state_file, model=args.model, reserve_rpd=args.reserve_rpd)
    chunks_in = gp / "chunks_in"
    chunks_out = gp / "chunks_out"
    manifest_path = gp / "manifest.json"
    blocked_json = gp / "safety_blocked_segments.json"
    blocked_txt = gp / "safety_blocked_segments.txt"

    name_map, glossary = apiw.load_glossary(gp)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_chunk = {m["chunk"]: m for m in manifest}
    all_blocked = json.loads(blocked_json.read_text(encoding="utf-8")) if blocked_json.exists() else []
    target_chunks = set(args.chunks or [])

    loaded_orig: dict[str, list[dict]] = {}
    sub_tasks: list[tuple[str, int, int]] = []
    selected_keys: set[tuple[str, tuple[int, int]]] = set()

    if args.mode == "skipped":
        for cname in (args.chunks or []):
            if cname in by_chunk:
                orig = json.loads((chunks_in / cname).read_text(encoding="utf-8"))
                loaded_orig[cname] = orig
                for s in range(0, len(orig), args.sub_size):
                    sub_tasks.append((cname, s, min(len(orig), s + args.sub_size)))
    else:
        selected_segs = [
            b for b in all_blocked
            if (not target_chunks or b["chunk"] in target_chunks) and b["chunk"] in by_chunk
        ]
        for b in selected_segs:
            cname = b["chunk"]
            if cname not in loaded_orig:
                loaded_orig[cname] = json.loads((chunks_in / cname).read_text(encoding="utf-8"))
            ls, le = b["chunk_local_range"]
            selected_keys.add((cname, (ls, le)))
            for s in range(ls, le, args.sub_size):
                sub_tasks.append((cname, s, min(le, s + args.sub_size)))

    if not sub_tasks:
        print("No retry slices to run.")
        return

    print(f"API retry mode={args.mode}: {len(loaded_orig)} chunks, {len(sub_tasks)} slices (sub_size={args.sub_size}, model={args.model}/{args.thinking_level}, conc={args.concurrency})")

    def run_slice(task: tuple[str, int, int]) -> dict:
        cname, s, e = task
        src_file = by_chunk[cname]["source_file"]
        sl = loaded_orig[cname][s:e]
        try:
            entries = apiw.translate_lines(
                args.model, f"{cname}[{s}:{e}]", src_file, sl, name_map, glossary, thinking_level=args.thinking_level
            )
            return {"chunk": cname, "s": s, "e": e, "ok": True, "entries": entries}
        except Exception as exc:
            err = str(exc)
            return {"chunk": cname, "s": s, "e": e, "ok": False, "is_429": "quota_429" in err, "reason": err[:300]}

    slice_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for res in pool.map(run_slice, sub_tasks):
            if res.get("is_429"):
                print(f"  [429]  {res['chunk']}[{res['s']}:{res['e']}]: {res['reason']}")
                slice_results.append(res)
            else:
                slice_results.append(res)

    if args.mode == "skipped":
        processed = set(loaded_orig.keys())
        remaining_blocked = [b for b in all_blocked if b.get("chunk") not in processed]
    else:
        remaining_blocked = [
            b for b in all_blocked
            if (b["chunk"], tuple(b["chunk_local_range"])) not in selected_keys
        ]

    by_c_res: dict[str, list[dict]] = {}
    for r in slice_results:
        by_c_res.setdefault(r["chunk"], []).append(r)

    recovered = 0
    still_blocked = 0
    chunks_written = 0
    for cname, orig in loaded_orig.items():
        meta = by_chunk[cname]
        src_file = meta["source_file"]
        file_base = int(meta["start"])
        out_path = chunks_out / cname
        merged = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else list(orig)

        c_res = sorted(by_c_res.get(cname, []), key=lambda x: x["s"])
        if not c_res:
            continue
        for r in c_res:
            s, e = r["s"], r["e"]
            if r["ok"]:
                for off, ent in enumerate(r["entries"]):
                    merged[s + off] = ent
                recovered += (e - s)
                print(f"  [OK]   {cname}[{s}:{e}]: {e - s} lines")
            else:
                if not out_path.exists():
                    for off in range(e - s):
                        merged[s + off] = orig[s + off]
                still_blocked += (e - s)
                tag = "[429]" if r.get("is_429") else "[BLK]"
                print(f"  {tag:6s} {cname}[{s}:{e}]: {r['reason']}")
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
        out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        chunks_written += 1

    remaining_blocked.sort(key=lambda x: (x["chunk"], x["chunk_local_range"][0]))
    blocked_json.write_text(json.dumps(remaining_blocked, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(blocked_txt, "w", encoding="utf-8") as f:
        f.write("以下段落因触发内容安全拦截、拒翻或配额未完成而保留原句：\n")
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

    print(f"\nAPI retry done: chunks_updated={chunks_written}, recovered={recovered}, still_blocked={still_blocked}, total_blocked_segs={len(remaining_blocked)}")
    print(kqm.format_status_report(apiw.get_quota_manager(), args.model))


if __name__ == "__main__":
    main()
