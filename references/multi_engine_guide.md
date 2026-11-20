# Cross-Engine Visual Novel / Galgame Localization Guide

Use this guide together with [SKILL.md](../SKILL.md) and [engine_and_patching.md](./engine_and_patching.md) when localizing any VN engine beyond CSystem. Regardless of engine, always funnel extracted text through the unified **GalTransl (`gt_input/` -> `chunks_in/` -> `chunks_out/` -> `gt_output/`)** pipeline so `glossary.json`, `sync_recall_tm.py`, `translate_wave.py` / `api_translate_wave.py`, `retry_blocked.py` / `api_retry_blocked.py`, `check_progress.py`, and `audit_scripts.py --json-only` work identically across every game.

---

## 1. Engine Identification -> Extraction, Injection & Runtime Matrix

| Engine | Shipped Fingerprints | Unpack / Extract Tools | Inject / Repack Route | Encoding, Font & Runtime Requirements |
|---|---|---|---|---|
| **CSystem / TinkerBell** | `Arc00..06.dat`, `config.dat`, `render.dll`, `.a0/.f0` | `CSystemArc.exe unpack`, `VNTextPatch extractlocal` | `VNTextPatch insertlocal` (temp stage) + 1:1 `M`-aligned `.a0` rebuild -> `CSystemArc pack` | `.patch` bypass + `HKLM->HKCU` reg redirect + `CP_ACP->932` + `sjis_ext.bin` + `4xSS GetGlyphOutlineA` + GBK `.rdata` dialog strings |
| **Kirikiri 2 / Kirikiri Z** | `krkr.exe` / `tvp`, `data.xp3`, `*.ks`, `*.tjs`, `Config.tjs` | **GARbro** / **KrkrExtract** -> `VNTextPatch` (`krkr`) | `VNTextPatch insertlocal` -> repack `.xp3` (or loose `patch*.xp3` / `data/` override) | Usually UTF-16LE (KrkrZ/newer Krkr2) or CP932 -> patch `Config.tjs` / `System` fonts, or hook `CreateFontIndirectA` / FreeType; watch `[r]`, `[l]`, `[p]`, `@tag` |
| **Artemis Engine** | `*.pfs`, `root.pfs`, `.ast` / `.iat` (Lua tables) | **GARbro** / Artemis PFS unpacker -> `VNTextPatch` (`artemis`) | `VNTextPatch insertlocal` -> repack `.pfs` or drop loose override folder (`script/`) | Native UTF-8! Edit Lua font/UI config or bundle CJK TTF/OTF; preserve Lua table syntax and `{tag}`/`[ruby]` escapes |
| **SiglusEngine / RealLive** | `SiglusEngine.exe`, `Scene.pck`, `Gameexe.dat` | **SiglusExtract** (unpack `Scene.pck` & decrypt `Gameexe.dat`) | Rebuild `Scene.pck` (`UTF-16LE`) & `Gameexe.dat` | Native UTF-16LE; bypass DVD/DRM/locale checks in `.exe` via REA + `Gameexe.dat` region/font config |
| **BGI / Ethornell** | `BGI.exe`, `data01000.arc`, `*.bp` / `_bgi` bytecode | **GARbro** -> `VNTextPatch` (`bgi`) / `bgi_dumper` | Rebuild bytecode **with offset/pointer table fix** (`VNTextPatch` / BGI packer) | Older BGI defaults to CP932 (`GetGlyphOutlineA` + `sjis_ext` or GBK wrapper); newer BGI supports UTF-8; preserve `\n` & BGI control opcodes |
| **CatSystem2 (`cs2`)** | `cs2.exe`, `*.int`, `*.cst` (compressed scene scripts) | **GARbro** / `fes` (`.int` key via REA or `v_code`) -> `VNTextPatch` (`catsystem2`) | Rebuild `.cst` (preserve opcode/string ordering) -> `.int` or loose `scene/` | Often CP932 -> hook `CreateFontIndirectA`, `GetGlyphOutlineA`, `MultiByteToWideChar` or patch `sjis_ext`; preserve `\n`, `\fn` control codes |
| **WillPlus / AdvHD** | `AdvHD.exe`, `Rio.arc`, `Chip.arc`, `*.ws2` | **GARbro** -> `VNTextPatch` (`advhd` / `ws2`) | Rebuild `.ws2` cleanly (preserving jump offsets) -> `.arc` or `Rio/` override | AdvHD v1 uses CP932 (needs LE-free + `CP_ACP` + font hook); AdvHD v2 uses UTF-16LE (needs CJK font check + protection bypass) |
| **Qlie** | `*.pack`, `*.s`, `*.b`, `qlie` signatures | **GARbro** / Qlie extractor -> `VNTextPatch` (`qlie`) | Re-encode `.s` / `.b` preserving Qlie line/command IDs -> repack `.pack` | Watch hash validation inside `.pack` & CP932/GBK font charset |
| **YU-RIS** | `*.ypf`, `ysbin/*.ybn` | **GARbro** / `ybn` unpacker (extract XOR key via REA from `.exe`) -> `VNTextPatch` (`yuris`) | Rebuild `.ybn` string/command table with exact jump targets -> repack `.ypf` | CP932 / GBK hook or UTF-8 patched engine; inspect hardcoded `.exe` error/config strings via REA |
| **Majiro** | `*.arc`, `*.mjo` bytecode | **GARbro** / `mjdev` (`mjo` XOR key / disasm) | Reassemble `.mjo` updating string table & relative offsets -> `.arc` | Often CP932; watch opcode string lengths & font charset (`SHIFTJIS_CHARSET` -> `GB2312_CHARSET`) |
| **Unity (`Naninovel` / `Utage` / `Mono` / `IL2CPP`)** | `UnityPlayer.dll`, `GameAssembly.dll` or `Assembly-CSharp.dll`, `*.assets` | REA (`inspect_managed_artifact`, `inspect_managed_members`) + **AssetStudio / UABEA** + Naninovel `.nani` / Utage Excel/Asset extractors | Re-import edited `.nani` / `TextAsset` / `MonoBehaviour` tables into `.assets` / bundle | **TextMeshPro (`TMP_FontAsset`)** requires injecting a Simplified Chinese SDF Font Atlas fallback or characters become `□`; zero LE/CP932 issues |
| **Ren'Py** | `*.rpa`, `game/*.rpy` / `.rpyc` | `unrpa` + `unrpyc` | Official Ren'Py `tl/schinese/*.rpy` (no binary bytecode re-pack needed!) | Add Simplified Chinese TTF/OTF in `gui.rpy` (`gui.text_font` / `gui.name_text_font`); preserve `[var]` & `{tag}` interpolations cleanly |
| **Electron / NW.js / TyranoScript / RPG Maker MV/MZ** | `resources/app.asar`, `package.nw`, `www/data/*.json`, `tyrano/` | REA (`inspect_electron_fuses`, `analyze_javascript_application`) + `asar extract` / JSON load | Write translated JSON / `*.ks` cleanly back -> repack or drop unpacked `app/` | Native UTF-8; inspect JS font CSS (`@font-face`); preserve `\V[n]`, `\N[n]`, `\C[n]`, `[emb ...]`, `[l][r][p]` |

---

## 2. Universal Non-Script Localization Checklist

Translating main dialogue scripts alone often leaves visible Japanese or mojibake in **5 non-script places**:

1. **Duplicate Recollection / Scene-Replay Scripts**:
   - Many VNs copy main-story H-scenes or bonus episodes into separate script files (e.g., CSystem `000097`–`000210`).
   - **Rule**: Always check extracted scripts for `>= 80%` line overlap with main-story files. Translate main story first, then run `scripts/sync_recall_tm.py` to auto-fill Recollection copies for zero API cost.
2. **Standalone Choice / Menu / Backlog / Gallery Index Scripts**:
   - Branch menus, Recollection buttons, CG Gallery captions, and Config labels frequently live in high-numbered or dedicated scripts (`000213`, `000230..000233`, `000251`, `system.ks`, `ui.ast`, `common/*.rpy`). Audit ALL files emitted by extraction, not just `000001..0000NN`.
3. **Win32 Dialogs & Dynamic `ComboBox` / `Edit` Controls (`SendMessageA` / `SetDlgItemTextA`)**:
   - Even when static `.rsrc` `MENU`/`DIALOGEX` templates are translated to Unicode UTF-16LE, runtime code populating dropdowns (`CB_ADDSTRING`) or hardware/GPU info Edit boxes (`WM_SETTEXT`) often pushes raw **CP932 strings from `.rdata`**. Under Chinese Windows (`CP_ACP = 936`), `user32` decodes them as GBK -> **mojibake**.
   - Inspect dialog procedures via REA / xrefs and patch unhooked `.rdata` strings in-place to NUL-padded **GBK** (never touching strings matched by `.patch` lookup tables).
4. **Hardcoded EXE / DLL System Strings**:
   - Save/Load errors, QuickLoad confirmation, Exit prompts, Audio/DirectX initialization failures, and Window Title formatting.
5. **UI Textures / Graphics (Buttons, Tabs, Splash/Logos)**:
   - Unpack UI archives (`Arc02`/`Arc03`, `fgimage/`, `sys/`, `title/`) when buttons, config headers, or backlog labels are pre-rendered bitmaps (`.png`, `.tlg`, `.bmp`, `.webp`).

---

## 3. Cross-Engine Placeholder & Punctuation Audit Rules

When running LLM translation across heterogeneous engines, enforce these universal checks before repacking:
- **Preserve All Control Tokens 1:1**: `[r]`, `[l]`, `[p]`, `[np]`, `{w}`, `{p}`, `{nw}`, `%s`, `%d`, `%03d`, `\V[n]`, `\N[n]`, `\C[n]`, `\r\n` (`audit_scripts.py` verifies placeholder/tag parity automatically).
- **Preserve Whitespace-Only Formatting Entries**: Entries such as `"\u3000"` or `" "` control timing/spacing in many engines—keep them verbatim instead of stripping to `""`.
- **Clean Typography & Screams**: Replace katakana prolonged sound mark `ー` (`U+30FC`) with Chinese em-dash `——` (`U+2014`) in Chinese text (never leave kana inside SFX/screams), and watch codepage lookup characters (`sjis_ext.bin` or target codepage glyph tables) so rare CJK punctuation/glyphs never render as `?` or `□`.
- **Save & Config Portability**: Ensure `save/` and config files (`config.dat`, `setup.ini`, `save*.dat`) use relative paths under `game_portable/` rather than failing when registry keys (`HKLM\Software\...`) or Japanese `%APPDATA%` paths do not exist.
