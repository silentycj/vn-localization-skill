"""Audit GalTransl JSONs and (when present) repacked engine scripts against originals.

Usage:
    python audit_scripts.py [game_root_or_gt_project_dir] [--no-unpack] [--json-only]

Checks:
  1. GalTransl JSON quality (all engines): entry count parity, leaked `name_map` keys,
     literal `\\\\r\\\\n` / `\\\\n` escapes, stray brackets, placeholder/tag parity
     (`%s`/`%d`, `{tag}`, `\\V[n]`/`\\N[n]`/`\\C[n]`), and kana leftovers.
  2. CSystem binary script integrity (auto-enabled when `work/scenarios/*.a0` exists,
     unpacking `game_portable/Arc01.dat` + `Arc04.dat` by default or `work/out_scenarios`
     with `--no-unpack`): untranslated files byte-identical, 1:1 chunk count, identical
     `M` opcodes / `<4c>` labels, and exact `0xfe` terminators.
"""

import argparse
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
RUBY_RE = re.compile(r"\[([^/\[\]]+)/([^\[\]]+)\]")
PH_RE = re.compile(r"%(?:\d+\$)?[sd]|{[^{}]+}|\\[VNC]\[\d+\]")


def resolve_paths(target: str | None) -> tuple[Path, Path]:
    if target:
        p = Path(target).resolve()
        if (p / "manifest.json").exists():
            root = p.parent.parent if p.parent.name.startswith("work") else p
            return root, p
        if (p / "work" / "gt_project" / "manifest.json").exists():
            return p, p / "work" / "gt_project"
    cwd = Path.cwd().resolve()
    if (cwd / "manifest.json").exists():
        root = cwd.parent.parent if cwd.parent.name.startswith("work") else cwd
        return root, cwd
    if (cwd / "work" / "gt_project" / "manifest.json").exists():
        return cwd, cwd / "work" / "gt_project"
    for cand in sorted(cwd.glob("*/work/gt_project/manifest.json")):
        return cand.parent.parent.parent, cand.parent
    raise FileNotFoundError("Could not locate game root or gt_project containing manifest.json.")


def parse_chunks(data: bytes) -> list[tuple[int, bytes]]:
    pos = 0
    recs: list[tuple[int, bytes]] = []
    while pos < len(data):
        clen = struct.unpack_from("<I", data, pos)[0]
        chunk = data[pos + 4 : pos + 4 + clen]
        recs.append((chunk[0], chunk[1:]))
        pos += 4 + clen
    if pos != len(data):
        raise ValueError(f"Unaligned .a0 stream: parsed {pos} of {len(data)} bytes")
    return recs


def dec_s(payload: bytes) -> bytes:
    slen = struct.unpack_from("<I", payload, 0)[0]
    enc = payload[4 : 4 + slen]
    key = slen & 0xFF
    return bytes(b ^ key for b in enc)


def decode_m(payload: bytes) -> str:
    p = 0
    tokens = []
    while p < len(payload):
        t = chr(payload[p])
        p += 1
        if t in ("#", "$", "&"):
            tokens.append(t)
        elif t == "N":
            val = struct.unpack_from("<i", payload, p)[0]
            p += 4
            tokens.append(f"N({val})")
        elif t == "S":
            slen = struct.unpack_from("<I", payload, p)[0]
            p += 4
            enc = payload[p : p + slen]
            p += slen
            dec = bytes(b ^ (slen & 0xFF) for b in enc)
            tokens.append(f"S({dec.hex()})")
        else:
            tokens.append(f"<{ord(t):02x}>")
    return " ".join(tokens)


def audit_json(gp: Path) -> tuple[int, int]:
    gt_in = gp / "gt_input"
    gt_out = gp / "gt_output"
    chunks_out = gp / "chunks_out"
    manifest = json.loads((gp / "manifest.json").read_text(encoding="utf-8"))
    glossary_path = gp / "glossary.json"
    name_map = (
        json.loads(glossary_path.read_text(encoding="utf-8")).get("name_map", {})
        if glossary_path.exists()
        else {}
    )
    blocked_path = gp / "safety_blocked_segments.json"
    blocked = json.loads(blocked_path.read_text(encoding="utf-8")) if blocked_path.exists() else []
    blocked_ranges: dict[str, set[int]] = {}
    for b in blocked:
        fs, fe = b["file_entry_range"]
        blocked_ranges.setdefault(b["source_file"], set()).update(range(fs, fe))

    # Map fully translated chunks per source_file to distinguish partial tails
    translated_ranges: dict[str, set[int]] = {}
    for item in manifest:
        if (chunks_out / item["chunk"]).exists():
            start = int(item["start"])
            end = int(item["end"]) if "end" in item else start + int(item["count"])
            translated_ranges.setdefault(item["source_file"], set()).update(range(start, end))

    errors = 0
    warnings = 0
    print("=== 1. GalTransl JSON Audit ===")
    for out_p in sorted(gt_out.glob("*.json")):
        in_rows = json.loads((gt_in / out_p.name).read_text(encoding="utf-8"))
        out_rows = json.loads(out_p.read_text(encoding="utf-8"))
        if len(in_rows) != len(out_rows):
            print(f"  [ERROR] {out_p.name}: row count mismatch {len(in_rows)} vs {len(out_rows)}")
            errors += 1
            continue

        tl_set = translated_ranges.get(out_p.name, set())
        blk_set = blocked_ranges.get(out_p.name, set())
        for idx, (a, b) in enumerate(zip(in_rows, out_rows)):
            msg = b.get("message", "")
            if not msg:
                print(f"  [ERROR] {out_p.name}[{idx}]: empty message")
                errors += 1
            if "\\r\\n" in msg or "\\n" in msg:
                print(f"  [ERROR] {out_p.name}[{idx}]: literal newline escape {msg!r}")
                errors += 1
            if idx in tl_set:
                orig_n = a.get("name")
                if orig_n in name_map and b.get("name") != name_map[orig_n]:
                    print(f"  [ERROR] {out_p.name}[{idx}]: unmapped name {b.get('name')!r} (expected {name_map[orig_n]!r})")
                    errors += 1
                for ruby_m in RUBY_RE.finditer(msg):
                    if KANA_RE.search(ruby_m.group(1)) or "・" in ruby_m.group(1):
                        print(f"  [WARN]  {out_p.name}[{idx}]: untranslated ruby/emphasis-dot markup {ruby_m.group(0)!r} in {msg!r}")
                        warnings += 1
                cleaned_no_ruby = RUBY_RE.sub("", msg)
                if "[" in cleaned_no_ruby or "]" in cleaned_no_ruby:
                    # Only flag unmatched brackets when source did not already carry engine [tag] directives
                    if "[" not in a.get("message", "") and "]" not in a.get("message", ""):
                        print(f"  [WARN]  {out_p.name}[{idx}]: dangling bracket in {msg!r}")
                        warnings += 1
                orig_ph = sorted(PH_RE.findall(a.get("message", "")))
                if orig_ph and sorted(PH_RE.findall(msg)) != orig_ph:
                    print(f"  [ERROR] {out_p.name}[{idx}]: placeholder/tag mismatch orig={orig_ph} vs {msg!r}")
                    errors += 1
                if idx not in blk_set and KANA_RE.search(cleaned_no_ruby):
                    # Ignore single-char Japanese quote glyph shapes like 「く」字形
                    if "「く」" not in cleaned_no_ruby and "゛" not in cleaned_no_ruby and "・" not in cleaned_no_ruby:
                        print(f"  [WARN]  {out_p.name}[{idx}]: kana leftover {msg!r}")
                        warnings += 1

    print(f"  JSON audit finished: {len(list(gt_out.glob('*.json')))} gt_output files, errors={errors}, warnings={warnings}")
    return errors, warnings


def audit_scripts(game_root: Path, gp: Path, orig_dir: Path, portable_dir: Path, unpack: bool) -> int:
    gt_out = gp / "gt_output"
    translated_stems = {p.stem for p in gt_out.glob("*.json")}
    translated_files = {f"{s}.a0" for s in translated_stems}

    tmp_dir: Path | None = None
    if unpack:
        arc_exe = game_root / "tools" / "CSystemArc.exe"
        arc01 = portable_dir / "Arc01.dat"
        arc04 = portable_dir / "Arc04.dat"
        tmp_dir = Path(tempfile.mkdtemp(prefix="vn_script_audit_"))
        subprocess.run(
            [str(arc_exe), "unpack", str(arc01), str(arc04), str(tmp_dir)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        target_dir = tmp_dir
        source_label = f"unpacked {portable_dir.name}/Arc01.dat+Arc04.dat"
    else:
        target_dir = gp.parent / "out_scenarios"
        source_label = str(target_dir)

    errors = 0
    try:
        orig_map = {p.name: p for p in orig_dir.iterdir() if p.is_file()}
        pack_map = {p.name: p for p in target_dir.iterdir() if p.is_file()}
        if set(orig_map) != set(pack_map):
            missing = sorted(set(orig_map) - set(pack_map))
            extra = sorted(set(pack_map) - set(orig_map))
            print(f"  [ERROR] File set mismatch vs {source_label}: missing={missing[:5]}, extra={extra[:5]}")
            return 1

        untranslated_diffs = []
        jump_issues = []
        fe_issues = []
        struct_issues = []

        for name, op in sorted(orig_map.items()):
            ob = op.read_bytes()
            pb = pack_map[name].read_bytes()
            if name not in translated_files:
                if ob != pb:
                    untranslated_diffs.append(name)
                continue

            ro = parse_chunks(ob)
            rp = parse_chunks(pb)
            if len(ro) != len(rp):
                struct_issues.append((name, f"chunk_count {len(ro)}->{len(rp)}"))
                continue

            labels_orig = {i for i, (t, pl) in enumerate(ro) if t == 0x4D and pl == b"\x4c\x4e\x00\x00\x00\x00"}
            labels_pack = {i for i, (t, pl) in enumerate(rp) if t == 0x4D and pl == b"\x4c\x4e\x00\x00\x00\x00"}
            if labels_orig != labels_pack:
                jump_issues.append((name, "label_positions_changed"))

            for idx, ((to, plo), (tp, plp)) in enumerate(zip(ro, rp)):
                if to != tp:
                    struct_issues.append((name, f"chunk[{idx}] type {to:#x}->{tp:#x}"))
                    break
                if to != 0x53:
                    if plo != plp:
                        struct_issues.append((name, f"chunk[{idx}] M payload diff: {decode_m(plo)} vs {decode_m(plp)}"))
                        break
                else:
                    dec = dec_s(plp)
                    expected_fe = b"\xff\xff" not in dec
                    if dec.endswith(b"\xfe") != expected_fe:
                        fe_issues.append((name, idx, f"expected_fe={expected_fe}"))
                        if len(fe_issues) >= 10:
                            break

        print(f"\n=== 2. Binary Script Audit ({source_label}) ===")
        print(f"  Total files checked: {len(orig_map)} ({len(translated_files)} translated .a0)")
        print(f"  Untranslated files modified: {len(untranslated_diffs)} {untranslated_diffs[:10]}")
        print(f"  Structural / chunk-index / M-opcode mismatches: {len(struct_issues)} {struct_issues[:10]}")
        print(f"  Label (<4c>) position mismatches: {len(jump_issues)} {jump_issues[:10]}")
        print(f"  0xfe terminator mismatches: {len(fe_issues)} {fe_issues[:10]}")
        errors = len(untranslated_diffs) + len(struct_issues) + len(jump_issues) + len(fe_issues)
    finally:
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit GalTransl JSONs and repacked engine scripts")
    parser.add_argument("target", nargs="?", help="Game root or gt_project directory")
    parser.add_argument("--dir", "--project", dest="proj_flag", help="Explicit game root or gt_project directory")
    parser.add_argument("--orig-dir", help="Override unpacked original scenarios directory")
    parser.add_argument("--portable-dir", help="Override portable output directory containing Arc01.dat/Arc04.dat")
    parser.add_argument("--no-unpack", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    game_root, gp = resolve_paths(args.proj_flag or args.target)
    unpack = not args.no_unpack
    json_only = args.json_only
    print(f"Auditing game root: {game_root} (gt_project: {gp})")
    j_err, _ = audit_json(gp)

    orig_dir = Path(args.orig_dir).resolve() if args.orig_dir else (gp.parent / "scenarios")
    if not orig_dir.exists():
        orig_dir = game_root / "work" / "scenarios"
    if args.portable_dir:
        portable_dir = Path(args.portable_dir).resolve()
    elif gp.parent.name == "work":
        portable_dir = game_root / "game_portable"
    else:
        variant = gp.parent.name[5:] if gp.parent.name.startswith("work_") else gp.parent.name
        cands = [
            p for p in sorted(game_root.glob("*_portable"))
            if p.name != "game_portable" and (p / "Arc01.dat").exists()
        ]
        matched = [p for p in cands if variant.lower() in p.name.lower()]
        portable_dir = matched[0] if matched else (cands[0] if len(cands) == 1 else Path("__none__"))

    has_csystem_scripts = orig_dir.exists() and any(orig_dir.glob("*.a0"))
    can_unpack = (
        (game_root / "tools" / "CSystemArc.exe").exists()
        and (portable_dir / "Arc01.dat").exists()
        and (portable_dir / "Arc04.dat").exists()
    )
    if json_only or not has_csystem_scripts or (unpack and not can_unpack):
        print("\n=== 2. Binary Script Audit ===\n  Skipped CSystem .a0 unpack audit (JSON-only mode or non-CSystem project layout).")
        s_err = 0
    else:
        s_err = audit_scripts(game_root, gp, orig_dir, portable_dir, unpack=unpack)

    if j_err + s_err > 0:
        sys.exit(1)
    print("\nALL SCRIPT AUDITS PASSED CLEANLY.")


if __name__ == "__main__":
    main()
