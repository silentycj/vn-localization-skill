# Engine ID, Extraction, GalTransl, REA, Portable EXE & Font Reference

## 1. Engine Identification & Script Extraction Tools

### Step A: Identify Engine & Format
Combine **REA** binary inspection with game directory fingerprints:
1. **REA (`rea` MCP / `reverse-engineer-anything`)**:
   - `open_binary` -> `binary_overview` & `inspect_binary_layout`: inspect sections, imports, compilers, entry points, and ASLR/PE flags.
   - `search_strings` & `list_names`: spot archive filenames (`Arc00.dat`..`Arc06.dat`, `config.dat`), engine DLLs (`render.dll`), vendor registry paths (`Software\TinkerBell\...`), or engine strings.
   - `inspect_pe_resources`: inspect embedded menu/dialog resources for UI localization.
2. **Directory & Archive Fingerprints**:
   - Presence of `Arc00.dat`..`Arc06.dat` + `config.dat` + `render.dll` -> **CSystem** engine (`TinkerBell` etc.).
   - Verify with `CSystemArc.exe` (`unpack` -> outputs `.a0` scenario scripts plus `.f0`/`.l0`/`.m0`/`.e0`/`.p0`/`.x0`/`.q0`/`.t0`/`.v0` resources; version `23` for `Arc01.dat`/`Arc04.dat` repacking).

### Step B: Extraction & Packing Toolchain (`tools/`)
- **`tools/CSystemArc.exe`** (Archive unpack/pack & config):
  - `CSystemArc unpack <index.dat> <content1.dat> [content2.dat ...] <out_folder>`
  - `CSystemArc pack <version> <in_folder> <index.dat> <content1.dat> [content2.dat ...]` (e.g. version `23`, outputs `Arc01.dat` and `Arc04.dat`)
  - `CSystemArc readconfig config.dat config.xml` / `writeconfig config.xml config.dat`
- **`tools/VNTranslationTools/VNTextPatch/VNTextPatch.exe`** (Script JSON extractor/injector):
  - `VNTextPatch extractlocal <script_folder> <gt_input_folder>` -> generates GalTransl-compatible JSON files (`000001.json` .. `000251.json`)
  - `VNTextPatch insertlocal <orig_script_folder> <merged_json_folder> <out_script_folder>` -> injects translated JSON back into `.a0` and emits **`sjis_ext.bin`** (extended CP932 character lookup table needed by the game runtime)
  - Note: `VNTranslationTools/VNTextProxy` provides reference proxy DLLs (`msacm32.dll`, `version.dll`, etc.), but for a clean standalone build, prefer baking runtime hooks directly into the EXE instead of shipping extra proxy DLL dependencies where possible.

---

## 2. GalTransl Project Structure & Translation Integration

All script translation state lives in a standard **`GalTransl` (`work/gt_project/`)** layout:
- `gt_input/*.json`: original extracted Japanese scripts, array of entries:
  ```json
  [
    { "message": "地の文……" },
    { "name": "深琴", "message": "「セリフ……」" }
  ]
  ```
- `gt_output/*.json`: translated script JSONs matching `gt_input` entry order (`merge_and_pack.py` generates/updates these from `chunks_out/`).
- `chunks_in/` & `manifest.json`: 100-line uniform slices created by `work/prepare_chunks.py` for reliable parallel workers.
- `chunks_out/`: translated chunk JSONs (plus Japanese fallback only for still-blocked slices).
- `glossary.json`: project-specific terminology and deterministic speaker-name map, organized by fixed translation categories:
  ```json
  {
    "name_map": { "JapaneseSpeaker": "简体中文人名" },
    "glossary": "【人物名称/称呼】...\n【道具/物品】...\n【地点/机构】...\n【其他专有名词/术法/种族】..."
  }
  ```
- **GalTransl Fixed Translation Rules (人物 / 道具 / 地点 / 其他专有名词)**:
  - **人物名称与称呼**：`name_map` 中的说话人姓名及旁白/对话内提及的人名（如 `深琴`、`武`（深琴的双胞胎妹妹）、`ヤマト -> 大和`、`蟲十郎 -> 虫十郎`、`女子生徒 -> 女学生`）全篇统一，禁止同一角色出现多个译名或日文假名残留。
  - **道具与法器名称**：重要物品、护符、神器/材料（如 `御札/護符 -> 符咒/护符`、`ヒヒイロカネ/緋緋色金 -> 绯绯色金`）固定译法，保留原意与专名质感。
  - **地点与组织机构**：神社、地名、设施、界域名称（如 `御剣神社 -> 御剑神社`）全篇固定一致。
  - **其他专有名词与术语**：退魔术式/戒禁名、神名、种族/怪物名（如 `退魔師 -> 退魔师`、`霊力 -> 灵力`、`淫妖蟲 -> 淫妖虫`、`時量師神 -> 时量师神`、`ナイトメア -> 梦魇`）、音译词及专有设定词必须入表统一。
  - **格式与标签校验**：保持 `\r\n` 换行符不被转义为 `\\r\\n`；清理 `[reading/base]` 注音或着重号标记（如 `[・・・/下半身がなくなっていく]`）时确保同时剥离 `[`、`/`、`]` 与日文假名/中点，不遗漏原注音括号也不丢失正文词义；注入前须用 `name_map` 兜底校对未替换的说话人字段。
- `skipped_chunks.json` & `safety_blocked_segments.{json,txt}`: tracks chunks/slices that refused or hit content safety filters so non-blocked lines are never held hostage.

**How `GalTransl`, Recollection TM & `run_workflow` work together**:
- Both read `gt_input/` and emit standard `gt_output/` JSONs, so you can either translate chunks via this skill's `run_workflow` scripts (`translate_wave.py` -> `retry_blocked.py`, always `model="inherit"` + `humanizer-zh`) or external multi-key API workers (`<= 200`-line chunks when optimizing daily RPD caps), then run `scripts/sync_recall_tm.py [gt_project] --recover-blocked` to auto-fill duplicate Recollection/Replay files (`000097`–`000210` in CSystem) and cross-recover blocked slices, and finally run `work/merge_and_pack.py` -> `work/build_patch.py` to produce `game_portable/Arc01.dat`, `Arc04.dat`, and `sjis_ext.bin`.

### Step C: Original-vs-Translated Script Verification & Anti-Loop Injection
Because `VNTextPatch.exe insertlocal` (a) word-wraps long strings into extra consecutive `S` chunks, (b) strips trailing `0xfe` bytes from `S` chunks, and (c) rewrites every `.a0` file whose `.json` is passed in, naive `insertlocal` shifts script chunk indices (`# N(100) N(target) ... <4c> N(0)`, `# N(1)`, `# N(50)`) and causes backward jumps (infinite loops):
1. **Stage Only Translated JSONs**: Copy originals cleanly first, then run `VNTextPatch insertlocal` inside a temp folder containing only translated `.a0` + `.json` pairs solely to encode extended CJK bytes and build `sjis_ext.bin`.
2. **1:1 `M`-Aligned Reconstruction (`work/build_patch.py`)**: Align `ORIG` and `VNTextPatch` chunks by their identical `M` command stream, merge wrapped `S` segments back into the exact original `S` chunk slots (splitting only original multi-`S` entries like `【Speaker】` + dialogue or `\r\n` double-`S` records). When gallery/manual `.a0` blocks collapse leading centering spaces (`\x81@\xfe`) or multi-line wraps so `len(pat_s_list) < len(orig_s_list)` (e.g., `000251.a0`), preserve leading whitespace-only `orig` `S` slots in place, write `pat_s_list`, and pad trailing extra slots with empty `S` (`_enc_s_chunk(b"")` -> `b"\xfe"`). Restore `0xfe` on every non-`\xff\xff` `S` chunk, and re-XOR with `slen & 0xFF`.
3. **Post-Pack Unpack Verification (`scripts/audit_scripts.py`)**: Run `python ".agents/skills/vn-localization/scripts/audit_scripts.py" "<game_root_or_gt_project>" [--portable-dir <dir>]` to unpack `Arc01.dat` + `Arc04.dat` with `CSystemArc.exe unpack` and verify `0` modified untranslated files, `0` chunk-count or `M`-opcode diffs, `0` missing `0xfe` terminators (distinguishing original `\xff\xff` ruby chunks vs stripped-ruby rebuilt `0x53` `'S'` chunks), intact `<4c> N(0)` jump indices, and clean GalTransl JSON formatting (`name_map`, `\r\n`, leftover `[・・・/...]` ruby/emphasis-dot brackets).
4. **Third-Party Patch Conflict Check**: Before applying or merging an external third-party translation patch (such as partial `VNTextPatch` + `winmm.dll` proxy archives), inspect script coverage and hook targets. Never overwrite or stack an older external proxy patch on top of a complete standalone EXE + 1:1 `M`-aligned rebuild, as mixing `winmm.dll`/`msacm32.dll` hooks and mismatched script/slot layouts introduces regressions.

---

## 3. REA Reverse Engineering Checklist for PE Patching
Use `rea` tools before modifying any executable bytes:
1. **Binary Layout**: `open_binary`, `inspect_binary_layout` (`ImageBase`, `AddressOfEntryPoint`, section slack for `.patch`, `DllCharacteristics` `DYNAMIC_BASE`).
2. **Protections & Registry**: `find_xrefs_to_name` / `search_strings` for `RegOpenKeyExA`, `RegQueryValueExA`, vendor keys (`Software\<Vendor>\<Game>`), `GetDriveTypeA`; inspect callers with `analyze_function`, `procedure_pseudo_code`, `procedure_assembly` and assert pre-patch bytes with `PatchBytesChecked`.
3. **Locale & UI**: Trace `GetUserDefaultUILanguage`, `GetSystemDefaultLangID`, `GetCPInfo`, `MultiByteToWideChar`; inspect PE menus/dialogs (`inspect_pe_resources`); locate IAT slots (e.g. `.rdata` range protected via `VirtualProtect`).
4. **Font & Rendering**: Trace `CreateFontIndirectA`, `EnumFontFamiliesExA`, renderer DLL exports/imports (`render.dll` `CreateFontA`), `GetGlyphOutlineA`, and glyph pixel/alpha blending loops in `procedure_assembly`.

---

## 4. Standalone Portable EXE, Registry Removal & LE Elimination

### Converting EXE to Standalone Portable Build
1. **Append `.patch` Section**: `0xE0000060` (`CODE | INITIALIZED_DATA | EXECUTE | READ | WRITE`), clear `IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE` (`0x0040`), point `AddressOfEntryPoint` to `EntryStub` (`NewOEP`) and update `SizeOfImage`.
2. **`EntryStub` Initialization**: Resolve needed Win32 APIs (`VirtualProtect`, `GetModuleHandleA`, wide UI APIs, `GetCurrentObject`, real `CreateFontIndirectA`), install inline/IAT hooks (and patch loaded `render.dll` IAT via `GetModuleHandleA`), then jump to `origOep`.
3. **Direct `.text` Patches & Checksum**: Apply verified byte patches (`PatchBytesChecked`) and recompute PE checksum via `imagehlp.dll!CheckSumMappedFile`. For bonus/fandisc EXEs sharing the same engine DLLs (`render.dll`, `msacm32.dll`), always re-verify `.text`/`.rdata`/IAT VAs per executable via REA rather than reusing main-game addresses verbatim.

### Removing Windows Registry Dependency
- **Bypass Install Check**: Patch the registry verification branch in `.text` (e.g., `JNZ fail` -> `NOP; JMP pass`).
- **Redirect HKLM Opens (`Hook_RegOpenKeyExA`)**: Inline-hook `RegOpenKeyExA` to intercept `HKEY_LOCAL_MACHINE (0x80000002)` queries for the vendor key (`Software\<Vendor>`) and redirect to a benign user key (`HKCU (0x80000001)` + `"Environment"`), eliminating installer/admin prerequisites.

### Eliminating Locale Emulator (LE) Dependency
- **Locale & Codepage**: Hook `GetUserDefaultUILanguage`/`GetSystemDefaultLangID` -> `0x0411`; hook `GetCPInfo` so `CP_ACP (0)`/`CP_OEMCP (1)` maps to `932` (matching `sjis_ext.bin`).
- **UI Localization**: Embed localized binary menu/dialog blobs and CP932->UTF-16 string tables in `.patch`; hook `FindResourceA`/`LoadResource`/`SizeofResource`, `LoadMenuA` -> `LoadMenuIndirectW`, `MessageBoxA` -> `MessageBoxW`, `SendMessageA` (`TCM_INSERTITEMA`) -> `SendMessageW`, `SetWindowTextA` -> `SetWindowTextW`.
- **Save/Load Slot Preview & GDI `DrawTextA` / `ExtTextOutA` Hook**: Proxy DLLs such as `VNTextProxy` (`msacm32.dll`) often hook `TextOutA`/`GetGlyphOutlineA` only, leaving `USER32.dll!DrawTextA`, `DrawTextExA`, and `GDI32.dll!ExtTextOutA` unhooked. Save/Load screens (`"Data %02d %s"`, timestamps, and `sjis_ext.bin`-encoded story previews) rendered through those GDI/User32 APIs will turn into CP936 mojibake on Simplified Chinese Windows. Expand `.patch` (if needed, updating `SizeOfRawData`, `SizeOfImage`, and PE `CheckSum`), load `sjis_ext.bin` in `EntryStub` (mapping valid trail bytes `0x01..0x3F`, skipping `0x09, 0x0A, 0x0D, 0x20, 0x2C`), and hook IAT `DrawTextA`, `DrawTextExA`, and `ExtTextOutA` to decode `sjis_ext.bin` + `CP932` into UTF-16LE before forwarding to `*W` counterparts.
- **Dynamic `DIALOGEX` Controls (`CB_ADDSTRING` / `Edit` GPU Info)**: Unicode `DIALOGEX` controls populated via `SendMessageA`/`SetDlgItemTextA` (e.g., Fullscreen Texture Filtering ComboBox and Direct3D Hardware Support Edit box) decode ANSI strings using the OS active codepage (`CP936` GBK) rather than EXE IAT hooks. Locate the `.rdata` CP932 strings pushed by the dialog proc (do NOT modify strings matched by `.patch`'s CP932->UTF-16 lookup table such as Tab headers), and patch them in-place to NUL-padded Simplified Chinese **GBK** (shorter byte lengths, preserving `\r\n`, `\t`, `%dx%d`).

---

## 5. Chinese Font Rendering Effects & Visual Quality Polish
Japanese engines typically request Shift-JIS raster/TrueType defaults that clip, bloat, distort stroke weight, or show jagged outlines on Simplified Chinese glyphs. Apply these four fixes and verify visually:

1. **Font Selection, Charset & Em-Height Normalization (`Hook_CreateFontIndirectA` / `Hook_RenderCreateFontA`)**:
   - Force `FaceName = "Microsoft YaHei"`, `CharSet = 0x86 (GB2312_CHARSET)`, `Quality = 4 (ANTIALIASED_QUALITY)`, `PitchAndFamily = 0x22 (VARIABLE_PITCH | FF_SWISS)`, `Width = 0`.
   - Convert positive cell height to negative Em height: `-(h * 7 + 4) / 8` so Chinese glyphs fit vertically inside text boxes without bottom clipping or cramped baselines.
   - Hook `EnumFontFamiliesExA` to satisfy initial engine Japanese font enumeration queries (`ＭＳ ゴシック`, `SHIFTJIS_CHARSET`).
2. **Outline Weight Clamping**:
   - Patch/clamp `lfWeight == 900 (FW_HEAVY)` -> `400 (FW_NORMAL)` in both `Hook_CreateFontIndirectA` and `.text` font setup instructions so dense Chinese characters do not bloat into unreadable blobs.
3. **`4x` Supersampled `GetGlyphOutlineA` + `0.80` Gamma LUT**:
   - Handles both `GGO_GRAY8_BITMAP (6)` and `GGO_GRAY4_BITMAP (4)` on unrotated identity transforms (`0 < |lfHeight| <= 60`).
   - Rasterizes glyphs via GDI at `lfHeight * 4` (cached across 8 `(origH, origWeight, style)` HFONT slots), runs a `4x4` box-filter downsample into target dimensions, and maps coverage through a **`0.80` gamma LUT**—eliminating `msyh.ttc` small/medium-size TrueType hinting stroke-weight jumps (such as horizontal/vertical thickness glitches in `算`, `管`, `笼`, `罩`).
4. **Smooth Porter-Duff Alpha Compositing (`Hook_SmoothAlpha`)**:
   - Detour the engine glyph blit loop to replace hard `dstA = 0xFF` alpha snapping with smooth Porter-Duff Over compositing (`outA = min(255, srcA * (255 - dstA) / 255 + dstA)`), yielding crisp, halo-free Chinese dialogue text and outlines over bright and dark backgrounds alike.
5. **Visual & Runtime Verification**:
   - Launch the patched EXE directly inside `game_portable/` on Simplified Chinese Windows (no LE, no registry) and verify dialogue rendering with tricky strokes (`算`, `管`, spaces) via live `GetGlyphOutlineA` checks or captured screenshots.

## CSystem Append Archives (`.app`/`.dat`) & Proxy DLL Hardening

1. **Append Scenario Extraction & Unified `sjis_ext.bin` Repacking (`append.ini`)**:
   - CSystem titles with append scenarios (`append.ini` -> `app/*.dat` + `app/*.app`) store extra `.a0` scripts inside the `.app` archive rather than `Arc04.dat`.
   - `CSystemArc.exe unpack` can throw `ArgumentOutOfRangeException` on delta image entries (`type b0`) when unpacking `.app` standalone, aborting before `.a0` entries. Use [csystem_append.py](../scripts/csystem_append.py) (`extract` / `pack`, powered by `CSystemArc.ArchiveReader` / `ArchiveWriter.WriteIndex()` via .NET reflection) to extract `.a0` entries directly (`Type == 'a'`) and repack rebuilt `.a0` files back into `.app` / `.dat`.
   - Stage **both** main-game (`Arc04.dat`) and append (`.app`) JSONs in a single `VNTextPatch.exe insertlocal` pass so `sjis_ext.bin` covers every CJK character across both campaigns. Reconstruct append `.a0` files with `_reconstruct_a0`, append them to `.app`, and update `Offset`/`CompressedSize`/`UncompressedSize` via `CSystemArc.ArchiveWriter.WriteIndex()`.
2. **Proxy DLL Fallback, Complete Exports & `codeCursor` Alignment**:
   - When an unpacked EXE drops `VERSION.dll` from its IAT (or when appending an RWX `.patch` section + new OEP triggers AV false positives such as `Trojan:Win32/Wacatac.B!ml`), use an IAT-imported non-KnownDLL proxy such as `winmm.dll` without modifying the EXE.
   - Forward **all** exports of the target system DLL (e.g., all 192 exports of `SysWOW64\winmm.dll`, not only the 5 statically imported `time*` functions): DirectX/audio components (`d3dx9_42.dll`, `dsound.dll`) resolve additional exports via `GetProcAddress` and can fail (e.g., `D3DXCreateFontIndirect FALSE`) if the proxy is incomplete.
   - Set `codeCursor = (exports.Length * 8 + 15) & ~15` (and shift `dataCursor` accordingly) so export jump stubs never overwrite `DllMain` or hook code (`0xc0000142`).
3. **Full Win32 `DIALOGEX` & Common Control Localization**:
   - Hook `TextOutA` -> `TextOutW`, `SetDlgItemTextA` -> `SetDlgItemTextW`, and `SendMessageA` -> `SendMessageW` for `WM_SETTEXT` (`0x000C`), `CB_ADDSTRING`/`CB_INSERTSTRING`, `LB_ADDSTRING`/`LB_INSERTSTRING`, `TCM_INSERTITEMA` (`0x1307` -> `0x133E`), `LVM_INSERTCOLUMNA` (`0x101B` -> `0x1061`), and `LVM_INSERTITEMA`/`LVM_SETITEMA`/`LVM_SETITEMTEXTA` (`0x1007`/`0x1006`/`0x102E` -> `*W`).
   - To localize Unicode `DIALOGEX` templates in `.rsrc` at runtime without patching the EXE, hook `ShowWindow` filtered strictly to the `#32770` dialog class (`GetClassNameA`) + `EnumChildWindows` (`GetWindowTextW` -> `UiTableW` -> `SetWindowTextW`). Without the `#32770` filter, `ShowWindow` calls on the main game window trigger continuous `EnumChildWindows` traversals and cause severe startup/runtime lag; with `#32770` filtering enabled, lag disappears completely (and 4× supersampled `GetGlyphOutlineW` itself only costs ~`142 ms` per 1,000 glyphs, so no offline `glyph_cache.bin` is needed).
