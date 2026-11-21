"""Extract and repack CSystem append scenarios (.dat + .app) via CSystemArc.exe .NET reflection."""

import argparse
import subprocess
import sys
from pathlib import Path


def run_ps(script: str) -> None:
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        text=True,
    )
    if res.returncode != 0:
        sys.exit(res.returncode)


def cmd_extract(args: argparse.Namespace) -> None:
    dat_path = Path(args.dat).resolve()
    app_path = Path(args.app).resolve()
    out_dir = Path(args.out_dir).resolve()
    arc_exe = Path(args.csystemarc).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    ps = f"""
$ErrorActionPreference = "Stop"
$asm = [Reflection.Assembly]::LoadFrom("{arc_exe}")
$rt = $asm.GetType("CSystemArc.ArchiveReader")
$flags = [Reflection.BindingFlags]"Instance,Public,NonPublic,CreateInstance"
$ar = [Activator]::CreateInstance($rt, $flags, $null, @("{dat_path}", [string[]]@("{app_path}")), $null)
$cnt = 0
foreach ($e in $ar.Entries) {{
    if ($e.Type -eq '{args.entry_type}') {{
        $bytes = $ar.GetEntryContent($e)
        $fname = "{{0:d06}}.{{1}}{{2}}" -f $e.Id, $e.Type, $e.SubType
        [IO.File]::WriteAllBytes((Join-Path "{out_dir}" $fname), $bytes)
        $cnt++
    }}
}}
$ar.Dispose()
Write-Host "Extracted $cnt .{args.entry_type}0 entries to {out_dir}"
"""
    run_ps(ps)


def cmd_pack(args: argparse.Namespace) -> None:
    dat_path = Path(args.dat).resolve()
    app_path = Path(args.app).resolve()
    in_dir = Path(args.in_dir).resolve()
    arc_exe = Path(args.csystemarc).resolve()
    orig_dat = Path(args.orig_dat).resolve() if args.orig_dat else dat_path.with_suffix(".dat.orig")
    orig_app_size = args.orig_app_size if args.orig_app_size else app_path.stat().st_size

    ps = f"""
$ErrorActionPreference = "Stop"
if (-not (Test-Path "{orig_dat}")) {{ Copy-Item "{dat_path}" "{orig_dat}" }}
$asm = [Reflection.Assembly]::LoadFrom("{arc_exe}")
$rt = $asm.GetType("CSystemArc.ArchiveReader")
$wt = $asm.GetType("CSystemArc.ArchiveWriter")
$flags = [Reflection.BindingFlags]"Instance,Public,NonPublic,CreateInstance"
$priv = [Reflection.BindingFlags]"Instance,NonPublic"

$ar = [Activator]::CreateInstance($rt, $flags, $null, @("{orig_dat}", [string[]]@("{app_path}")), $null)
$entries = @($ar.Entries)
$ar.Dispose()

$fs = [IO.File]::Open("{app_path}", [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite)
if ($fs.Length -gt {orig_app_size}L) {{ $fs.SetLength({orig_app_size}L) }}
$fs.Seek(0, [IO.SeekOrigin]::End) | Out-Null

$updated = 0
foreach ($e in $entries) {{
    if ($e.Type -eq '{args.entry_type}') {{
        $fname = "{{0:d06}}.{{1}}{{2}}" -f $e.Id, $e.Type, $e.SubType
        $p = Join-Path "{in_dir}" $fname
        if (Test-Path $p) {{
            $bytes = [IO.File]::ReadAllBytes($p)
            $off = [int]$fs.Position
            $fs.Write($bytes, 0, $bytes.Length)
            $e.Offset = $off
            $e.CompressedSize = $bytes.Length
            $e.UncompressedSize = $bytes.Length
            $updated++
        }}
    }}
}}
$fs.Close()

$tmpDat = "{dat_path}.tmp"
$tmpApp = "{app_path}.dummy"
$aw = [Activator]::CreateInstance($wt, $flags, $null, @(23, $tmpDat, [string[]]@($tmpApp)), $null)
$awEntries = $wt.GetField("_entries", $priv).GetValue($aw)
foreach ($e in $entries) {{ $awEntries.Add($e) | Out-Null }}
$wt.GetMethod("WriteIndex", [Reflection.BindingFlags]"Instance,Public,NonPublic").Invoke($aw, $null)
$aw.Dispose()
Remove-Item $tmpApp -ErrorAction SilentlyContinue
Move-Item -Force $tmpDat "{dat_path}"
Write-Host "Updated $updated .{args.entry_type}0 entries in {app_path} and {dat_path}"
"""
    run_ps(ps)


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract/repack CSystem append (.dat + .app) archives")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", help="Extract entries by type (default 'a' for .a0 scripts)")
    ex.add_argument("--csystemarc", required=True, help="Path to CSystemArc.exe")
    ex.add_argument("--dat", required=True, help="Path to append .dat index")
    ex.add_argument("--app", required=True, help="Path to append .app archive")
    ex.add_argument("--out-dir", required=True, help="Output directory")
    ex.add_argument("--entry-type", default="a", help="Entry type char (default: a)")
    ex.set_defaults(func=cmd_extract)

    pk = sub.add_parser("pack", help="Append rebuilt entries to .app and update .dat index")
    pk.add_argument("--csystemarc", required=True, help="Path to CSystemArc.exe")
    pk.add_argument("--dat", required=True, help="Path to append .dat index")
    pk.add_argument("--app", required=True, help="Path to append .app archive")
    pk.add_argument("--in-dir", required=True, help="Directory with rebuilt .a0 files")
    pk.add_argument("--orig-dat", default="", help="Optional backup path for original .dat")
    pk.add_argument("--orig-app-size", type=int, default=0, help="Original .app byte size for idempotent truncation")
    pk.add_argument("--entry-type", default="a", help="Entry type char (default: a)")
    pk.set_defaults(func=cmd_pack)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
