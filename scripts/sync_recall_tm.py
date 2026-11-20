"""Auto-fill exact-match duplicate chunks, recover blocked TM hits, and optionally rechunk todo slices.

Usage:
    python sync_recall_tm.py [gt_project_dir] [--recover-blocked] [--rechunk-todo 200]
"""

import argparse
import json
import re
from pathlib import Path

HAS_JP_OR_CJK = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


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
    raise FileNotFoundError("Could not locate gt_project containing manifest.json.")


def build_tm(gp: Path, manifest: list[dict]) -> dict[tuple[str, str], tuple[str | None, str]]:
    chunks_in = gp / "chunks_in"
    chunks_out = gp / "chunks_out"
    tm: dict[tuple[str, str], tuple[str | None, str]] = {}
    for m in manifest:
        out_p = chunks_out / m["chunk"]
        if not out_p.exists():
            continue
        src_rows = json.loads((chunks_in / m["chunk"]).read_text(encoding="utf-8"))
        dst_rows = json.loads(out_p.read_text(encoding="utf-8"))
        for sr, dr in zip(src_rows, dst_rows):
            if dr["message"] != sr["message"]:
                tm.setdefault((sr.get("name", ""), sr["message"]), (dr.get("name"), dr["message"]))
    return tm


def rechunk_todo_slices(gp: Path, target_size: int) -> None:
    gt_input = gp / "gt_input"
    chunks_in = gp / "chunks_in"
    chunks_out = gp / "chunks_out"
    manifest_path = gp / "manifest.json"
    skipped_path = gp / "skipped_chunks.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    skipped = set(json.loads(skipped_path.read_text(encoding="utf-8"))) if skipped_path.exists() else set()

    by_file: dict[str, list[dict]] = {}
    file_order: list[str] = []
    for m in manifest:
        sf = m["source_file"]
        if sf not in by_file:
            by_file[sf] = []
            file_order.append(sf)
        by_file[sf].append(m)

    new_manifest: list[dict] = []
    merged_groups = 0
    removed_chunks = 0
    for sf in file_order:
        items = sorted(by_file[sf], key=lambda x: int(x["start"]))
        i = 0
        while i < len(items):
            cur = items[i]
            cname = cur["chunk"]
            if (chunks_out / cname).exists() or cname in skipped:
                new_manifest.append(cur)
                i += 1
                continue
            group = [cur]
            total = int(cur["count"])
            j = i + 1
            while j < len(items):
                nxt = items[j]
                nc = nxt["chunk"]
                if (chunks_out / nc).exists() or nc in skipped or total + int(nxt["count"]) > target_size:
                    break
                group.append(nxt)
                total += int(nxt["count"])
                j += 1
            if len(group) > 1:
                combined = []
                for g in group:
                    combined.extend(json.loads((chunks_in / g["chunk"]).read_text(encoding="utf-8")))
                first = group[0]
                (chunks_in / first["chunk"]).write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
                for g in group[1:]:
                    (chunks_in / g["chunk"]).unlink(missing_ok=True)
                    removed_chunks += 1
                merged_groups += 1
                new_manifest.append({"chunk": first["chunk"], "source_file": sf, "start": int(first["start"]), "count": len(combined)})
            else:
                new_manifest.append(cur)
            i = j

    check_by_file: dict[str, list[dict]] = {}
    for m in new_manifest:
        check_by_file.setdefault(m["source_file"], []).append(m)
    for sf, clist in check_by_file.items():
        gt_rows = json.loads((gt_input / sf).read_text(encoding="utf-8"))
        pos = 0
        for m in clist:
            assert int(m["start"]) == pos, f"{sf}: expected start={pos}, got {m['start']}"
            c_rows = json.loads((chunks_in / m["chunk"]).read_text(encoding="utf-8"))
            assert len(c_rows) == int(m["count"]) and c_rows == gt_rows[pos : pos + len(c_rows)]
            pos += len(c_rows)
        assert pos == len(gt_rows)

    manifest_path.write_text(json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    todo = [m for m in new_manifest if not (chunks_out / m["chunk"]).exists() and m["chunk"] not in skipped]
    print(f"Rechunked todo slices to <= {target_size} lines: merged={merged_groups}, removed={removed_chunks}, todo_chunks={len(todo)}, todo_lines={sum(m['count'] for m in todo)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", nargs="?", help="Path to gt_project or game root")
    parser.add_argument("--recover-blocked", action="store_true")
    parser.add_argument("--rechunk-todo", type=int, default=0, help="Merge untouched consecutive slices up to N lines")
    args = parser.parse_args()

    gp = resolve_gt_project(args.project)
    recover_blocked = args.recover_blocked

    chunks_in = gp / "chunks_in"
    chunks_out = gp / "chunks_out"
    manifest = json.loads((gp / "manifest.json").read_text(encoding="utf-8"))
    skipped_path = gp / "skipped_chunks.json"
    skipped = set(json.loads(skipped_path.read_text(encoding="utf-8"))) if skipped_path.exists() else set()
    glossary_path = gp / "glossary.json"
    name_map = json.loads(glossary_path.read_text(encoding="utf-8")).get("name_map", {}) if glossary_path.exists() else {}

    tm = build_tm(gp, manifest)
    filled_chunks = 0
    filled_lines = 0

    for m in manifest:
        cname = m["chunk"]
        if (chunks_out / cname).exists() or cname in skipped:
            continue
        src_rows = json.loads((chunks_in / cname).read_text(encoding="utf-8"))
        if not src_rows:
            continue
        out_rows = []
        ok = True
        for sr in src_rows:
            key = (sr.get("name", ""), sr["message"])
            if key in tm:
                dn, dm = tm[key]
                rec = {}
                if "name" in sr:
                    rec["name"] = name_map.get(sr["name"], dn or sr["name"])
                rec["message"] = dm
                out_rows.append(rec)
            elif not HAS_JP_OR_CJK.search(sr["message"]):
                rec = {}
                if "name" in sr:
                    rec["name"] = name_map.get(sr["name"], sr["name"])
                rec["message"] = sr["message"]
                out_rows.append(rec)
            else:
                ok = False
                break
        if ok:
            (chunks_out / cname).write_text(json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8")
            filled_chunks += 1
            filled_lines += len(out_rows)

    print(f"Auto-filled exact-match chunks: {filled_chunks} chunks ({filled_lines} lines)")

    if recover_blocked:
        blocked_json = gp / "safety_blocked_segments.json"
        blocked_txt = gp / "safety_blocked_segments.txt"
        if blocked_json.exists():
            by_chunk = {m["chunk"]: m for m in manifest}
            tm = build_tm(gp, manifest)
            blks = json.loads(blocked_json.read_text(encoding="utf-8"))
            remaining = []
            tm_hits = 0
            for b in blks:
                cname = b["chunk"]
                s, e = b["chunk_local_range"]
                orig_rows = json.loads((chunks_in / cname).read_text(encoding="utf-8"))
                out_rows = json.loads((chunks_out / cname).read_text(encoding="utf-8"))
                leftover = []
                for idx in range(s, e):
                    sr = orig_rows[idx]
                    key = (sr.get("name", ""), sr["message"])
                    if key in tm:
                        dn, dm = tm[key]
                        rec = {}
                        if "name" in sr:
                            rec["name"] = name_map.get(sr["name"], dn or sr["name"])
                        rec["message"] = dm
                        out_rows[idx] = rec
                        tm_hits += 1
                    else:
                        leftover.append(idx)
                (chunks_out / cname).write_text(json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8")
                if leftover:
                    base = int(by_chunk[cname]["start"])
                    run_s = leftover[0]
                    run_prev = leftover[0]
                    for idx in leftover[1:] + [None]:
                        if idx is not None and idx == run_prev + 1:
                            run_prev = idx
                            continue
                        remaining.append(
                            {
                                "chunk": cname,
                                "source_file": b["source_file"],
                                "chunk_local_range": [run_s, run_prev + 1],
                                "file_entry_range": [base + run_s, base + run_prev + 1],
                                "line_count": run_prev - run_s + 1,
                                "reason": b["reason"],
                                "sample_first": orig_rows[run_s]["message"],
                                "sample_last": orig_rows[run_prev]["message"],
                            }
                        )
                        if idx is not None:
                            run_s = run_prev = idx
            blocked_json.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")
            lines_txt = [
                f"{b['source_file']} [{b['file_entry_range'][0]}:{b['file_entry_range'][1]}] "
                f"({b['chunk']} {b['chunk_local_range'][0]}:{b['chunk_local_range'][1]}, {b['line_count']} lines) | {b['reason']}\n"
                f"  first: {b['sample_first']}\n"
                f"  last : {b['sample_last']}\n"
                for b in remaining
            ]
            blocked_txt.write_text("\n".join(lines_txt), encoding="utf-8")
            print(f"Blocked TM recovery: recovered={tm_hits} lines, remaining_segments={len(remaining)}")

    if args.rechunk_todo > 0:
        rechunk_todo_slices(gp, args.rechunk_todo)


if __name__ == "__main__":
    main()
