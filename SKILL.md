---
name: vn-localization
description: >-
  End-to-end Visual Novel / Galgame localization workflow: identify engine & archive formats using REA (`reverse-engineer-anything`) and package fingerprints, extract/repack scripts via `CSystemArc` and `VNTextPatch` into a GalTransl (`gt_input`/`gt_output`) project, build a standalone portable EXE free of protections, Windows Registry checks, and Locale Emulator (LE) dependencies with polished CJK font rendering (4x supersampled `GetGlyphOutlineA`, `0.80` gamma LUT, weight clamping, smooth Porter-Duff alpha), and translate scripts via `run_workflow` (`model="inherit"` + `humanizer-zh`) with layered safety-refusal slicing. Activate when identifying a VN engine, extracting scripts, setting up or translating a GalTransl project, patching EXE protections/registry/LE/fonts, retrying blocked segments, checking progress, or repacking.
---

# Visual Novel Localization Workflow (Engine ID + Script Extraction + REA + Portable EXE + GalTransl + Font Polish)

Follow this end-to-end workflow to inspect the game engine, extract scripts into a GalTransl project, convert the binary into a standalone registry-free & LE-free EXE with crisp Chinese font rendering, translate naturally with `humanizer-zh`, isolate refused slices cleanly, and pack back into the game.

## Core Requirements & Invariants

1. **Engine Identification & Binary Inspection via REA**:
   - Inspect shipped binaries (`.exe`, engine DLLs such as `render.dll`) with `rea` MCP tools (`open_binary`, `binary_overview`, `inspect_binary_layout`, `search_strings`, `find_xrefs_to_name`, `analyze_function`, `procedure_pseudo_code`, `procedure_assembly`, `inspect_pe_resources`) and verify archive/file signatures before designing extraction or PE patches.
2. **Standard Script Extraction & GalTransl Layout**:
   - Unpack engine archives (e.g., `CSystemArc.exe unpack`), extract editable JSONs (`VNTextPatch.exe extractlocal`) directly into GalTransl `gt_input/*.json` (`[{"name": ..., "message": ...}]`), chunk with `prepare_chunks.py` into `chunks_in/` + `manifest.json`, and keep `gt_output/*.json` compatible with GalTransl tooling.
3. **GalTransl Fixed Translation Rules (Characters / Items / Locations / Terms)**:
   - Maintain a strict category-complete glossary in `<gt_project>/glossary.json` covering **人物名称/称呼 (Characters)**、**道具/物品 (Items)**、**地点/机构 (Locations)**、**其他专有名词/招式/种族/世界观术语 (Other Proper Nouns & Terms)**, alongside deterministic speaker mapping (`name_map`).
   - Enforce 100% consistent translations across dialogue, narration, choices, and speaker boxes; never paraphrase, mix variants, or leave `name_map` entries unmapped. Check for literal `\\r\\n` escapes and broken `[reading/base]` ruby tags before packing.
4. **Original Script Audit & Zero-Index-Shift Packing Invariant (Anti-Loop Rule)**:
   - Before/after packing, always audit translated scripts against original unpacked scripts (`.a0`). Never allow injector line-wrapping (`VNTextPatch insertlocal`) to split single `S` records into multiple `S` records or strip trailing `0xfe` terminators, and never touch untranslated `.a0` files. CSystem opcodes such as `# N(100)`, `# N(1)`, `# N(50)` store **chunk indices** (`<4c> N(0)` labels): shifting indices turns forward/wait jumps into backward jumps (infinite loops).
   - Reconstruct `.a0` files by aligning `M` records 1:1 with the original `.a0` (preserving exact chunk counts/indices and `0xfe` rules, splitting only genuine multi-`S` slots like `\r\n` pairs), then unpack and verify the packed `.dat` scripts byte/structure-wise against originals.
5. **Standalone Portable EXE (No External Injector / Protection Checks)**:
   - Bake all hooks and byte patches into the `.exe` directly (append `.patch` section, clear `DYNAMIC_BASE`, route `NewOEP` -> `EntryStub` -> original `OEP`, assert pre-patch bytes via `PatchBytesChecked`, recompute PE `CheckSum`). Do not depend on external loaders, temporary proxy DLLs, or installer/CD checks.
6. **Zero Windows Registry & Locale Emulator (LE) Dependencies**:
   - Bypass install registry checks in `.text` and inline-hook `RegOpenKeyExA` to redirect vendor `HKLM` queries cleanly without admin rights or registry writes.
   - Hook locale/codepage APIs (`GetUserDefaultUILanguage`/`GetSystemDefaultLangID` -> `0x0411`, `GetCPInfo` `CP_ACP`/`CP_OEMCP` -> `932` alongside `sjis_ext.bin`) and UI APIs (`LoadMenuA`, `FindResourceA`, `LoadResource`, `SizeofResource`, `MessageBoxA`, `SendMessageA`, `SetWindowTextA` -> Unicode `*W`) so the EXE runs directly on non-Japanese Windows without LE.
7. **Chinese Font Rendering Quality**:
   - Unify font creation (`CreateFontIndirectA` / renderer `CreateFontA`) to `Microsoft YaHei`, `GB2312_CHARSET (0x86)`, `ANTIALIASED_QUALITY (4)`, normalized negative Em height, and clamp `900` (`FW_HEAVY`) outline weight down to `400`.
   - Fix TrueType hinting stroke-weight inconsistency (e.g., dense strokes like `算`, `管`) via `4x` supersampled `GetGlyphOutlineA` (`4x4` box downsample + `0.80` gamma LUT) and patch engine glyph compositing to smooth Porter-Duff Over alpha instead of hard alpha snapping.
8. **Final Runnable Localized EXE Deliverable**:
   - The workflow must end with a directly runnable standalone localized `.exe` inside `game_portable/` alongside the repacked game archives and extended glyph table (e.g., `Arc01.dat`, `Arc04.dat`, `sjis_ext.bin`), ready for users to double-click and play immediately without external patchers, registry imports, or LE.
9. **Translation Model & Quality Invariants**:
   - **Always use `model="inherit"`** (never downgrade to `flash`/`flash_lite` unless explicitly requested).
   - **Apply `/humanizer-zh` rules**: preserve facts/tone/emotion/agency/negatives; eliminate translationese; keep `「...」`, `（...）`, `……`, `——`.
   - **Never bypass safety filters via jailbreaks/obfuscation** and **never blindly overwrite `chunks_out/*.json`**.
   - **Mandatory Post-Batch Progress Report**: After finishing any translation batch/part (or split-retry pass), always run [check_progress.py](./scripts/check_progress.py) and report the current completion progress (`chunks_out`, file coverage, lines/percentage, and remaining safety-blocked counts) in the reply.
   - **Dynamic Usage-Based Chunk & Batch Allocation**: Dynamically adjust `batch_size` and `concurrency` according to real-time API usage, latency, and `429`/`RESOURCE_EXHAUSTED` feedback persisted in `usage_state.json`—scaling cleanly when headroom is healthy and backing off/halting early when rate pressure appears.
   - **Insufficient Quota Notification (Stop & Inform User)**: Whenever available model quota/capacity is insufficient to complete or continue translation (e.g., `quota_429` / `RESOURCE_EXHAUSTED` / `QUOTA_INSUFFICIENT` halt, or subagents fail due to quota/capacity exhaustion), **stop immediately without blind retries or model downgrades**, run [check_progress.py](./scripts/check_progress.py), and clearly inform the user of the insufficient available quota, current progress completed, and the exact resume point (`offset=0` skips completed chunks automatically once quota recovers).

---

## Step 1: Identify Engine & Extract Scripts into GalTransl Project

See [engine_and_patching.md](./references/engine_and_patching.md) for full CLI signatures and tool details:

1. **Identify Engine & Archive Version**:
   - Run REA (`open_binary`, `binary_overview`, `search_strings`) on the game `.exe` and inspect the game folder layout (e.g., `Arc00.dat`~`Arc06.dat` + `config.dat` + `render.dll` indicates CSystem / TinkerBell; check unpacked script extensions such as `.a0`/`.f0`/`.m0` and pack version such as `23`).
2. **Unpack & Extract to GalTransl Format (`gt_input`)**:
   - Unpack scenario archive (`CSystemArc.exe unpack <index.dat> <content.dat> <scenarios_dir>`).
   - Extract dialogue JSON (`VNTextPatch.exe extractlocal <scenarios_dir> <gt_project/gt_input>`).
   - Create/verify `<gt_project>/glossary.json` (`{"name_map": {...}, "glossary": "..."}`) and split into 100-line chunks via `prepare_chunks.py` (`chunks_in/` + `manifest.json`).
   - Result follows the standard GalTransl structure (`gt_input/`, `gt_output/`, `glossary.json`), interoperable with both `GalTransl` workflows and `run_workflow` scripts.

## Step 2: REA Reverse Engineering, Standalone Portable EXE & Font Polish

Verify every patch address and API slot with REA first (details in [engine_and_patching.md](./references/engine_and_patching.md)):

1. **Protections & Registry Removal**: Audit `RegOpenKeyExA`/`RegQueryValueExA`/DRM branches via `find_xrefs_to_name` and `procedure_pseudo_code`; patch install checks in `.text` and redirect residual `HKLM` opens cleanly in `EntryStub`.
2. **LE Elimination & UI Localization**: Audit `GetUserDefaultUILanguage`, `GetCPInfo`, and PE resources (`inspect_pe_resources`); embed localized menus/dialogs/string tables into `.patch` and hook UI APIs to `*W`.
3. **Font Quality & Glyph Effects**: Audit `CreateFontIndirectA`, renderer `CreateFontA`, `GetGlyphOutlineA`, and glyph alpha compositing; hook font properties, apply `4x` supersampled rasterization (`0.80` gamma LUT) + weight clamp (`900 -> 400`) + Porter-Duff Over alpha detour.
4. **Build & Verify Standalone EXE**: Append `.patch` section, set `NewOEP`, clear `DYNAMIC_BASE`, recompute PE checksum via `CheckSumMappedFile`, and verify launching the EXE directly inside `game_portable/` (no LE, no registry entries, no helper DLL) plus visual/glyph checks on dense Chinese characters.

## Step 3: Check Translation Progress

Run [check_progress.py](./scripts/check_progress.py):

```powershell
python ".agents/skills/vn-localization/scripts/check_progress.py" "<optional_gt_project_dir>"
```

## Step 4: Batch-by-Batch Translation & Recording Refusals (`translate_wave.py`)

Run [translate_wave.py](./scripts/translate_wave.py) via `run_workflow` (`WaitMsBeforeAsync=0`) to translate 100-line chunks (`chunks_in/` + `manifest.json`) in sequential batches:

1. **Adaptive Wave Sizing (`usage_state.json`)**: Default to `"batch_size": "auto"` (starting around `12` with adaptive concurrent windows, `model="inherit"`). `translate_wave.py` tracks live `elapsed`, throughput (`lines/s`), and `quota_429` rate-limit responses in `<gt_project>/usage_state.json`:
   - If `quota_429` occurs mid-wave, it immediately halves concurrency (and halts early if an entire window hits `429`) and halves the next `recommended_batch_size`.
   - If headroom is healthy (`0` errors, sustained high throughput), it gradually scales up to `15`; if latency rises (`< 5 lines/s`), it conservatively trims concurrency (`<= 8`). **Never run two waves concurrently** since both update shared files (`chunks_out/`, `skipped_chunks.json`, `usage_state.json`).
2. **Resume Without Overwrite**: Any chunk already present in `chunks_out/` is skipped automatically unless `overwrite=True`.
3. **Glossary & Style Enforcement**: Automatically loads `<gt_project>/glossary.json`, applies `/humanizer-zh` rules in the prompt, aligns `idx`, and maps speaker `name` deterministically via `name_map`.
4. **Skip-and-Record Policy (Never Block a Wave)**: If a 100-line chunk triggers `content_safety_filter`, `empty_items_refused`, `ended_without_finish_refused`, or `model_refused`, do not abort the batch and do not write a partial/broken file—record its chunk name into `<gt_project>/skipped_chunks.json` and keep translating the remaining chunks.
5. **Post-Batch Progress Output**: Immediately after each wave completes (and after retrying any skipped chunks / repacking), run [check_progress.py](./scripts/check_progress.py) and output the updated progress (chunks, full/partial files, lines & %, safety-blocked stats, plus real-time usage/recommended next batch size) to the user.
6. **Quota Exhaustion Handling**: If `translate_wave.py` or `retry_blocked.py` reports `QUOTA_INSUFFICIENT` / `quota_429` halt (or `ok=0` due to quota), stop scheduling further waves/retries, report the current progress and quota shortage directly to the user, and wait for quota recovery or user instruction (never switch away from `model="inherit"` silently).

Example `run_workflow` Args:
```json
{
  "batch_size": "auto",
  "offset": 0,
  "model": "inherit"
}
```

## Step 5: Layered Split-and-Retry for Refused Content (`retry_blocked.py`)

A 100-line chunk is often refused only because a few sensitive lines pull down the surrounding ordinary context. Use [retry_blocked.py](./scripts/retry_blocked.py) to peel back non-triggering lines layer by layer while keeping only genuine safety-filtered slices verbatim in Japanese:

1. **Stage A — Split `skipped_chunks.json` at `sub_size=20` (`mode="skipped"`)**:
   - Feed refused chunk names in groups keeping total concurrent slices `<= 25` per workflow call (e.g., 5 chunks x 5 slices).
   - **Passed 20-line slices** are translated and assembled into `chunks_out/<chunk>`.
   - **Still-refused 20-line slices** keep Japanese source lines verbatim from `chunks_in/` and are written to `safety_blocked_segments.json` + `safety_blocked_segments.txt` (recording `source_file`, `chunk`, `chunk_local_range`, `file_entry_range`, `reason`, `sample_first`, `sample_last`).
   ```json
   {
     "mode": "skipped",
     "chunks": ["chunk_0008.json", "chunk_0020.json"],
     "sub_size": 20,
     "model": "inherit"
   }
   ```
2. **Stage B — Progressively Refine `safety_blocked_segments.json` (`mode="blocked"`, `sub_size=10` -> `sub_size=5`)**:
   - Retry remaining entries at `sub_size=10`, then optionally `sub_size=5` for tiny stubborn blocks, grouping by non-overlapping `chunks` (`<= 20..25` slices per call) so `safety_blocked_segments.json` updates never collide.
   - **Incremental Merge Invariant**: Always load existing `chunks_out/<chunk>` as the base array and overwrite ONLY newly succeeded `[s:e]` slices—never overwrite previously translated parts of that chunk.
   - **Auto-Removal**: Any slice/sub-slice that passes is merged into `chunks_out/<chunk>` and automatically removed from `safety_blocked_segments.json` and `.txt`.
   - **Genuine Refusal Retention**: Never use jailbreak or obfuscation prompts. Only slices that still refuse at fine granularity remain verbatim in Japanese inside `chunks_out/` and stay accurately listed in `safety_blocked_segments.*`.
   ```json
   {
     "mode": "blocked",
     "chunks": ["chunk_0008.json", "chunk_0020.json"],
     "sub_size": 10,
     "model": "inherit"
   }
   ```

## Step 6: Merge (`gt_output`), Repack Archives & Deliver Runnable Localized EXE

Run `work/merge_and_pack.py` in the sandbox:

```powershell
python "<game_root>/work/merge_and_pack.py"
```

Verify and finalize the directly runnable localized executable deliverable:
1. Verify `merge_and_pack.py` exits `0` and updates `game_portable/Arc01.dat`, `Arc04.dat`, and `sjis_ext.bin`.
2. **Audit Repacked Scripts vs. Originals** via [audit_scripts.py](./scripts/audit_scripts.py):
   ```powershell
   python ".agents/skills/vn-localization/scripts/audit_scripts.py" "<game_root>"
   ```
   Confirm `0` errors: (a) untranslated `.a0` and non-`.a0` files are byte-identical to `work/scenarios/`; (b) translated `.a0` scripts preserve 1:1 total chunk count, `M` instruction sequences, chunk-index targets (`# N(100)`, `# N(1)`, `<4c> N(0)`), and `0xfe` terminators (`b"\xff\xff" not in dec`); (c) no leaked `name_map` keys, literal `\\r\\n`, or broken `[reading/base]` ruby tags.
3. Ensure the patched portable EXE (e.g., `<game_portable>/<Game>.exe` and/or `开始游戏.exe`) lives cleanly inside `game_portable/` alongside the repacked archives and `sjis_ext.bin`—requiring zero external injectors, zero Windows Registry setup, and zero Locale Emulator (LE).
4. Launch-test the final localized EXE directly inside `game_portable/` (verifying process responsiveness, localized UI/menus, CJK text & 4xSS font clarity) and run [check_progress.py](./scripts/check_progress.py) before reporting completion.
