"""Report exact file, chunk, and line translation progress for a gt_project directory."""

import json
import sys
from pathlib import Path


def resolve_project_dir() -> Path:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return Path(sys.argv[1])
    cwd = Path.cwd()
    direct = cwd / "work" / "gt_project"
    if (direct / "manifest.json").exists():
        return direct
    for cand in sorted(cwd.glob("*/work/gt_project")):
        if (cand / "manifest.json").exists():
            return cand
    if (cwd / "manifest.json").exists():
        return cwd
    raise FileNotFoundError("Could not locate gt_project/manifest.json; pass project_dir as argv[1].")


def main() -> None:
    gp = resolve_project_dir()
    gt_in = gp / "gt_input"
    gt_out = gp / "gt_output"
    chunks_in = gp / "chunks_in"
    chunks_out = gp / "chunks_out"
    manifest = json.load(open(gp / "manifest.json", "r", encoding="utf-8"))
    blocked_path = gp / "safety_blocked_segments.json"
    blocked = json.load(open(blocked_path, "r", encoding="utf-8")) if blocked_path.exists() else []

    in_files = sorted(gt_in.glob("*.json"))
    out_files = sorted(gt_out.glob("*.json"))
    in_chunks = sorted(chunks_in.glob("chunk_*.json"))
    out_chunks = sorted(chunks_out.glob("chunk_*.json"))

    total_lines = sum(len(json.load(open(f, "r", encoding="utf-8"))) for f in in_files)
    translated_chunk_lines = sum(len(json.load(open(c, "r", encoding="utf-8"))) for c in out_chunks)
    blocked_lines = sum(b.get("line_count", 0) for b in blocked)

    by_src = {}
    out_chunk_set = {c.name for c in out_chunks}
    for m in manifest:
        by_src.setdefault(m["source_file"], []).append(m["chunk"])

    full_files, partial_files, untouched = 0, [], 0
    for src, clist in sorted(by_src.items()):
        done = sum(1 for c in clist if c in out_chunk_set)
        if done == len(clist):
            full_files += 1
        elif done > 0:
            partial_files.append((src, done, len(clist)))
        else:
            untouched += 1

    eff = translated_chunk_lines - blocked_lines
    print(f"Project: {gp}")
    print(f"gt_input files: {len(in_files)} ({in_files[0].name} .. {in_files[-1].name})")
    print(f"gt_output packed files: {len(out_files)}")
    print(f"File coverage (chunks_out): {full_files} full, {len(partial_files)} partial {partial_files}, {untouched} untouched")
    if out_chunks:
        print(f"Chunks: {len(out_chunks)} / {len(in_chunks)} ({out_chunks[0].name} .. {out_chunks[-1].name})")
    else:
        print(f"Chunks: 0 / {len(in_chunks)}")
    print(f"Lines in chunks_out: {translated_chunk_lines} / {total_lines} ({translated_chunk_lines / total_lines * 100:.2f}%)")
    print(f"Safety-blocked: {len(blocked)} segments / {blocked_lines} lines")
    print(f"Effective translated: {eff} / {total_lines} ({eff / total_lines * 100:.2f}%)")


if __name__ == "__main__":
    main()
