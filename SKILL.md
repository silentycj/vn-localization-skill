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
   - Enforce 100% consistent translations across dialogue, narration, choices, and speaker boxes; never paraphrase, mix variants, or leave `name_map` entries unmapped. Check for literal `\\r\\n` escapes and broken or leaked `[reading/base]` / emphasis-dot `[・・・/...]` ruby tags before packing.
4. **Original Script Audit & Zero-Index-Shift Packing Invariant (Anti-Loop Rule)**:
   - Before/after packing, always audit translated scripts against original unpacked scripts (`.a0`). Never allow injector line-wrapping (`VNTextPatch insertlocal`) to split single `S` records into multiple `S` records or strip trailing `0xfe` terminators, and never touch untranslated `.a0` files. CSystem opcodes such as `# N(100)`, `# N(1)`, `# N(50)` store **chunk indices** (`<4c> N(0)` labels): shifting indices turns forward/wait jumps into backward jumps (infinite loops).
   - Reconstruct `.a0` files by aligning `M` records 1:1 with the original `.a0` (preserving exact chunk counts/indices and `0xfe` rules, splitting only genuine multi-`S` slots like `\r\n` pairs; when gallery/manual blocks collapse leading centering blanks `\x81@\xfe` or wrapped lines so `len(pat_s) < len(orig_s)`, preserve leading whitespace-only `S` slots and pad remaining wrapped slots with empty `S` `b"\xfe"`), then unpack and verify the packed `.dat` scripts byte/structure-wise against originals.
5. **Standalone Portable EXE (No External Injector / Protection Checks)**:
   - Bake all hooks and byte patches into the `.exe` directly (append `.patch` section, clear `DYNAMIC_BASE`, route `NewOEP` -> `EntryStub` -> original `OEP`, assert pre-patch bytes via `PatchBytesChecked`, recompute PE `CheckSum`). Do not depend on external loaders, temporary proxy DLLs, or installer/CD checks.
6. **Zero Windows Registry & Locale Emulator (LE) Dependencies**:
   - Bypass install registry checks in `.text` and inline-hook `RegOpenKeyExA` to redirect vendor `HKLM` queries cleanly without admin rights or registry writes.
   - Hook locale/codepage APIs (`GetUserDefaultUILanguage`/`GetSystemDefaultLangID` -> `0x0411`, `GetCPInfo` `CP_ACP`/`CP_OEMCP` -> `932` alongside `sjis_ext.bin`, and `MultiByteToWideChar` for newer 2017+ D2D/DWrite CSystem builds without `render.dll`/`GetGlyphOutlineA`) and UI/Save-Load GDI APIs (`LoadMenuA`/`LoadMenuW`, `FindResourceA`/`FindResourceW`/`FindResourceExW`, `LoadResource`, `SizeofResource`, `MessageBoxA`, `SendMessageA`, `SetWindowTextA`, plus IAT `DrawTextA`/`DrawTextExA`/`ExtTextOutA` guarding `lpString == NULL` with `sjis_ext.bin` + `CP932` -> `*W` so Save/Load previews never turn into CP936 mojibake) to run cleanly without LE.
   - When a CSystem rip ships `Arc05*`/`Arc06*`/`media/` inside `data/` alongside a root EXE, flatten everything into a single self-contained link-free folder (no NTFS hardlinks or junctions) so the build stays portable across drives and PCs.
   - Verify VAs per executable even for bonus/fandisc EXEs sharing the same DLLs, do not blindly merge partial third-party proxy patches (`winmm.dll`), and audit **dynamic Win32 `DIALOGEX` controls** (`SendMessageA(CB_ADDSTRING / WM_SETTEXT / TCM_INSERTITEMA / LVM_*)`, `SetDlgItemTextA`, `TextOutA`, plus `#32770`-filtered `ShowWindow` + `EnumChildWindows` for `.rsrc` Unicode dialogs): on Simplified Chinese Windows (`CP_ACP=936`), unhooked CP932 strings become mojibake unless converted/translated via `*W` hooks or in-place GBK patching. When a game uses `append.ini` (`.app`/`.dat`), stage main and append `.a0` scripts together in one `VNTextPatch insertlocal` pass so `sjis_ext.bin` stays unified; if an unpacked EXE drops `VERSION.dll` or triggers AV false positives on `.patch`, build a complete-export `winmm.dll` proxy (all 192 `SysWOW64\winmm.dll` exports, `codeCursor` aligned past `exports * 8`).
7. **Chinese Font Rendering Quality**:
   - Unify font creation (`CreateFontIndirectA` / renderer `CreateFontA`) to `Microsoft YaHei`, `GB2312_CHARSET (0x86)`, `ANTIALIASED_QUALITY (4)`, normalized negative Em height, and clamp `900` (`FW_HEAVY`) outline weight down to `400`.
   - Fix TrueType hinting stroke-weight inconsistency (e.g., dense strokes like `算`, `管`) via `4x` supersampled `GetGlyphOutlineA` (`4x4` box downsample + `0.80` gamma LUT) and patch engine glyph compositing to smooth Porter-Duff Over alpha instead of hard alpha snapping.
8. **Final Runnable Localized EXE Deliverable**:
   - The workflow must end with a directly runnable standalone localized `.exe` inside `game_portable/` alongside the repacked game archives and extended glyph table (e.g., `Arc01.dat`, `Arc04.dat`, `sjis_ext.bin`), ready for users to double-click and play immediately without external patchers, registry imports, or LE.
9. **Translation Model & Quality Invariants**:
   - **Always use `model="inherit"`** (never downgrade to `flash`/`flash_lite` unless explicitly requested).
   - **Default Token-Saving Mode (Always On by Default)**: All subsequent translation runs default to token-saving mode automatically via [translate_wave.py](./scripts/translate_wave.py) & [api_translate_wave.py](./scripts/api_translate_wave.py)—compact `idx\tspeaker\tmessage` payloads (with `name` omitted on output when `name_map` already maps the speaker), per-batch glossary/speaker filtering (injecting only terms present in the batch), **240-line / 16,000-char** coalesced batches (`coalesce_max_lines=240` / `--pack-lines 240`), zero-cost TM pre-fill/post-fill, and one-pass sensual/SFX quality so no full second polish wave is needed.
   - **Apply `/humanizer-zh`, sensual fidelity & natural SFX rules**: preserve facts/tone/emotion/agency/negatives and the original erotic tension, organ terminology, and **organ actions/reactions** faithfully without dry medical/textbook flattening or bloodless verbs (avoid `粘膜`/`突起`/`分泌物` or flat `触碰`/`进入`/`刺激`, using vivid tactile Chinese Galgame prose matching each character's voice); keep `「...」`, `（...）`, `……`, `——`; render Japanese katakana/hiragana SFX & cries into natural Chinese sound textures and cadence instead of syllable-by-syllable calques (e.g., avoid `ザアァ->沙啊啊`, `ひぃ->哈咿`, `あぐ->啊咕`, `ぎゃ->啊嘎`, `ぐえ->咕呃`, repetitive `滋溜溜溜`/`吸溜溜`, or excessive `～～～`).
   - **Never bypass safety filters via jailbreaks/obfuscation** and **never blindly overwrite `chunks_out/*.json`**.
   - **Mandatory Post-Batch Progress Report**: After finishing any translation batch/part (or split-retry pass), always run [check_progress.py](./scripts/check_progress.py) and report the current completion progress (`chunks_out`, file coverage, lines/percentage, and remaining safety-blocked counts) in the reply.
   - **Dynamic Usage-Based Chunk & Batch Allocation**: Dynamically adjust `batch_size` and `concurrency` according to real-time API usage, latency, and `429`/`RESOURCE_EXHAUSTED` feedback persisted in `usage_state.json`—scaling cleanly when headroom is healthy and backing off/halting early when rate pressure appears.
   - **Insufficient Quota Notification (Stop & Inform User)**: Whenever available model quota/capacity is insufficient to complete or continue translation (e.g., `quota_429` / `RESOURCE_EXHAUSTED` / `QUOTA_INSUFFICIENT` halt, or subagents fail due to quota/capacity exhaustion), **stop immediately without blind retries or model downgrades**, run [check_progress.py](./scripts/check_progress.py), and clearly inform the user of the insufficient available quota, current progress completed, and the exact resume point (`offset=0` skips completed chunks automatically once quota recovers).

---

## Step 1: Identify Engine & Extract Scripts into GalTransl Project

See [multi_engine_guide.md](./references/multi_engine_guide.md) for the **13-engine detection & extraction matrix** (`CSystem`, `Kirikiri 2/Z`, `Artemis`, `Siglus/RealLive`, `BGI/Ethornell`, `CatSystem2`, `WillPlus/AdvHD`, `Qlie`, `YU-RIS`, `Majiro`, `Unity/Naninovel/Utage`, `Ren'Py`, `Electron/TyranoScript/RPG Maker`) and [engine_and_patching.md](./references/engine_and_patching.md) for deep CSystem/PE CLI details:

1. **Identify Engine, Encoding & Archive Format**:
   - Run REA (`open_binary`, `binary_overview`, `search_strings`, `inspect_managed_artifact`, `inspect_electron_fuses`) on shipped binaries alongside package fingerprints (`Arc*.dat`, `*.xp3`, `*.pfs`, `Scene.pck`, `*.arc`, `*.int`, `*.ypf`, `*.rpa`, `app.asar`, `Assembly-CSharp.dll`).
2. **Unpack & Extract to Unified GalTransl Format (`gt_input`)**:
   - Unpack/extract via the matching engine toolchain (`CSystemArc` / `GARbro` / `KrkrExtract` / `SiglusExtract` / `unrpa+unrpyc` / `AssetStudio` + `VNTextPatch extractlocal`) directly into `<gt_project>/gt_input/*.json` (remembering non-mainline choice/menu/UI/gallery scripts and duplicate Recollection copies).
   - Create/verify `<gt_project>/glossary.json` (`{"name_map": {...}, "glossary": "..."}`) and slice into `chunks_in/` + `manifest.json` (`prepare_chunks.py` at `100` lines/chunk, or optionally merge untouched slices via `sync_recall_tm.py --rechunk-todo 200` when optimizing external daily RPD quotas).
   - Result follows standard GalTransl structure (`gt_input/`, `gt_output/`, `glossary.json`), interoperable with `GalTransl`, `run_workflow`, and external multi-key API workers.

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

1. **Adaptive Wave Sizing, Compact Payloads & 240-Line Coalescing (`usage_state.json`)**: Default to `"batch_size": "auto"` (starting around `12` with adaptive concurrent windows, `model="inherit"`), supporting **`coalesce_max_lines=240`** / **`coalesce_max_chars=16000`** (packing consecutive small chunks into 1 call and unpacking back into individual `chunks_out/` files) and `max_chunks="all"` / `"auto_continue": True` for multi-window execution inside one `run_workflow`. To minimize token usage, requests use a **compact `idx\tspeaker\tmessage` payload** (omitting redundant JSON keys/indentation and outputting only `{idx, message}` unless speaker is unmapped) plus **per-batch filtered glossary & speaker injection** (only terms/speakers present in the current batch), alongside zero-cost inline TM pre-fill/post-fill. `translate_wave.py` tracks live `elapsed`, throughput (`lines/s`), and `quota_429` in `<gt_project>/usage_state.json`:
   - If `quota_429` occurs mid-wave, it immediately halves concurrency (and halts early if an entire window hits `429`) and halves the next `recommended_batch_size`.
   - If headroom is healthy (`0` errors, sustained high throughput), it gradually scales up to `15`; if latency rises (`< 5 lines/s`), it conservatively trims concurrency (`<= 8`). **Never run two waves concurrently** since both update shared files (`chunks_out/`, `skipped_chunks.json`, `usage_state.json`).
2. **Resume Without Overwrite & Preserve Whitespace Lines**: Any chunk already present in `chunks_out/` is skipped automatically unless `overwrite=True`. Preserve whitespace-only source lines (such as `"\u3000"`) verbatim instead of `.strip()`ing them to empty strings and throwing `empty_msg_at_*`.
3. **Recollection / Replay Duplicate Deduplication (`sync_recall_tm.py`)**: Many VN engines (e.g., CSystem/TinkerBell) duplicate main-story H-scenes into standalone Recollection `.a0` scripts (`000097`–`000210`). Translate main-story scripts first, then run [sync_recall_tm.py](./scripts/sync_recall_tm.py) after major waves to auto-fill 100%-matched recall chunks from verified `chunks_out/` translations at zero API cost (keeping Recollection mode in Chinese alongside the story).
4. **Glossary & Style Enforcement**: Automatically loads `<gt_project>/glossary.json`, applies `/humanizer-zh` rules in the prompt, aligns `idx`, and maps speaker `name` deterministically via `name_map`.
5. **Skip-and-Record Policy (Never Block a Wave)**: If a chunk triggers `content_safety_filter`, `empty_items_refused`, `ended_without_finish_refused`, or `model_refused`, do not abort the batch and do not write a partial/broken file—record its chunk name into `<gt_project>/skipped_chunks.json` and keep translating the remaining chunks.
6. **Post-Batch Progress Output**: Immediately after each wave completes (and after retrying any skipped chunks / repacking), run [check_progress.py](./scripts/check_progress.py) and output the updated progress (chunks, full/partial files, lines & %, safety-blocked stats, plus real-time usage/recommended next batch size) to the user.
7. **External Multi-Key API Priority & Quota Exhaustion (`key_quota_manager.py` / `api_translate_wave.py` / `api_retry_blocked.py`)**: If a user-supplied API keys file (e.g., `--keys-file` outside the skill) is provided, **prefer the direct API key scripts first** (`api_translate_wave.py` / `api_retry_blocked.py`—remembering to run network calls with `BypassSandbox: true` when the sandbox blocks sockets); if those keys are unavailable or fail, ask the user before falling back to IDE subscription quota. If `translate_wave.py` or `retry_blocked.py` reports `QUOTA_INSUFFICIENT` / `quota_429` halt (or `ok=0` due to quota), stop scheduling further waves/retries, report the current progress and quota shortage directly to the user, and wait for quota recovery or user instruction (never switch away from `model="inherit"` silently). When running on user-supplied external AI Studio API keys (kept outside the repo via `--keys-file` or `GEMINI_API_KEYS` env var, never stored in the skill):
   - Use [key_quota_manager.py](./scripts/key_quota_manager.py) together with [api_translate_wave.py](./scripts/api_translate_wave.py) & [api_retry_blocked.py](./scripts/api_retry_blocked.py). Quotas apply **per Google Cloud project** (e.g., `gemini-4-hydrogen` free tier: `5 RPM / 250K TPM / 20 RPD`, resetting at `00:00 PT`; `thinkingLevel=HIGH` thinking tokens count toward TPM). `key_quota_manager.py` persists per-key/per-project `key_quota_state.json` (masked fingerprints only), enforces sliding 60s RPM/TPM pacing and `--reserve-rpd` / `--cap` budgets, distinguishes `PerDay` vs `RetryInfo.retryDelay` cooldowns, records `usageMetadata` tokens, and allocates chunks fairly by remaining RPD ratio without wasting RPD on dummy `generateContent` probes (`check-models` uses free `GET /v1beta/models`).
   - To optimize daily RPD limits and input/output tokens, `api_translate_wave.py` runs zero-cost TM pre-fill/post-fill automatically, uses compact `idx\tspeaker\tmessage` payloads with per-batch glossary filtering, dynamically coalesces consecutive pending chunks up to `--pack-lines 240` / `--pack-chars 16000` per API call, and supports `--batch-size 0` to drain exact remaining pool RPD cleanly without triggering `PerDay` failures:
   ```powershell
   python ".agents/skills/vn-localization/scripts/key_quota_manager.py" plan --project "<gt_project>" --keys-file "<keys.txt>" --model gemini-4-hydrogen
   python -u ".agents/skills/vn-localization/scripts/api_translate_wave.py" --project "<gt_project>" --keys-file "<keys.txt>" --model gemini-4-hydrogen --thinking-level HIGH --batch-size 0 --pack-lines 240 --concurrency 4
   python -u ".agents/skills/vn-localization/scripts/api_retry_blocked.py" --project "<gt_project>" --keys-file "<keys.txt>" --mode skipped --sub-size 20 --concurrency 3 --chunks chunk_0214.json
   ```

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
2. **Stage B — Progressively Refine & Cross-Recover `safety_blocked_segments.json`**:
   - First run `python ".agents/skills/vn-localization/scripts/sync_recall_tm.py" "<gt_project>" --recover-blocked`: slices blocked in one script context are often translated cleanly in their duplicate Recollection/Story script later, recovering most blocked lines immediately via TM.
   - Retry remaining entries (`mode="blocked"`, `sub_size=10` -> `sub_size=5` -> `1`-by-`1` for final stubborn lines), grouping by non-overlapping `chunks` (`<= 20..25` slices per call) so `safety_blocked_segments.json` updates never collide.
   - **Incremental Merge Invariant**: Always load existing `chunks_out/<chunk>` as the base array and overwrite ONLY newly succeeded `[s:e]` slices—never overwrite previously translated parts of that chunk.
   - **Auto-Removal**: Any slice/sub-slice that passes is merged into `chunks_out/<chunk>` and automatically removed from `safety_blocked_segments.json` and `.txt`.
   - **Genuine Refusal Retention & Optional NMT Fallback**: Never use jailbreak or obfuscation prompts. By default, slices that still refuse at fine granularity remain verbatim in Japanese inside `chunks_out/` and stay accurately listed in `safety_blocked_segments.*`. If the user requests 100% completion (or fallback machine translation), translate only those final `safety_blocked_segments` entries via Google Translate (`translate.googleapis.com`, `BypassSandbox: true`), preserving `\r\n` line breaks and `name_map`, then merge and repack.
   - **Skill Maintenance & GitHub Sync Rule**: Whenever updating the `vn-localization` skill and pushing to GitHub, update `SKILL.md`, `CHANGELOG.md`, and `README.md` together in the same release.
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

Verify and finalize the directly runnable localized build deliverable (adapting archive/runtime steps per [multi_engine_guide.md](./references/multi_engine_guide.md)):
1. Re-insert `gt_output/*.json` via the engine's packer (`VNTextPatch` + `build_patch.py` -> `Arc*.dat` + `sjis_ext.bin` for CSystem, `.xp3`/`.pfs`/`Scene.pck`/`tl/schinese/*.rpy`/`*.assets`/`app.asar` for other engines). Also verify non-script UI textures, menus, and dynamic Win32 dialog/ComboBox strings are localized without mojibake.
2. **Audit Repacked Scripts vs. Originals** via [audit_scripts.py](./scripts/audit_scripts.py):
   ```powershell
   python ".agents/skills/vn-localization/scripts/audit_scripts.py" "<game_root_or_gt_project>"
   ```
   Confirm `0` errors: JSON row parity, `name_map` integrity, no literal `\\r\\n`/`\\n`, placeholder/tag parity (`%s`/`%d`, `{tag}`, `\V[n]`), broken `[reading/base]` ruby tags, and—when CSystem `.a0` binaries are present—byte-identical untranslated files, 1:1 chunk counts/`M` opcodes/`<4c> N(0)` jump targets, and `0xfe` terminators (`--json-only` skips CSystem `.dat` unpacking on non-CSystem engines automatically).
3. Ensure the portable build (EXE/launcher, repacked data, font assets / `sjis_ext.bin`, relative `save/` paths) runs directly inside `game_portable/` without external injectors, registry setup, or Locale Emulator (LE).
4. Launch-test the final localized build directly inside `game_portable/` (verifying startup, story + Recollection/Gallery menus, Config dialogs/dropdowns, and CJK glyph rendering clarity) and run [check_progress.py](./scripts/check_progress.py) before reporting completion.
