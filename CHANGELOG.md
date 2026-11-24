# Changelog / 版本迭代记录

All notable changes to the **vn-localization** skill are documented in this file.

---

## [1.2.4] - 2026-11-24

### Added & Documented
- **2017+ CSystem D2D/DWrite/GDI Variant & `data/` Subfolder Layout** ([`SKILL.md`](./SKILL.md), [`references/engine_and_patching.md`](./references/engine_and_patching.md)):
  - Documented newer CSystem titles (e.g., *Shin Inyouchuu*) that omit `render.dll` and `GetGlyphOutlineA` in favor of `d2d1.dll` / `DWrite.dll` / `GdiPlus.dll` / `ExtTextOutA`: hook `MultiByteToWideChar` (`CodePage` `0`/`1`/`932`, `cbMultiByte == -1`) via `DecodeSjisExtToUtf16` and guard `lpString == NULL || c == 0` in `Hook_ExtTextOutA` (`CDC::FillSolidRect`).
  - Documented NTFS hardlinking (`mklink /H`) for `Arc05.dat`/`Arc05a.dat`/`Arc06.dat` and directory junction (`mklink /J`) for `media/` when a rip puts data archives in `data/` while also shipping an EXE at the root, preventing `CFile::Seek` -> `0xC0000409` crashes on double-click.
- **Default API Key Priority, Google Translate Fallback & GitHub Sync Convention** ([`SKILL.md`](./SKILL.md), [`README.md`](./README.md)):
  - Documented prioritizing external API key scripts (`api_translate_wave.py` / `api_retry_blocked.py`) when a `--keys-file` is provided (and asking the user before falling back to IDE quota if unavailable).
  - Documented using a post-processed Google Translate (`translate.googleapis.com`) fallback (preserving `\r\n` and `name_map`) exclusively for the tiny fraction of lines still blocked after fine-grained `retry_blocked.py` slicing when 100% coverage is requested.
  - Documented that any skill update pushed to GitHub must update `SKILL.md`, `CHANGELOG.md`, and `README.md` together.

---

## [1.2.3] - 2026-11-22

### Fixed & Documented
- **CSystem `EnumFontFamiliesExA` Callback & CRT `0xc0000417` Guard** ([`references/engine_and_patching.md`](./references/engine_and_patching.md)):
  - Documented patching the CSystem `EnumFontFamiliesExA` callback (`lfCharSet == 0x80` -> `0x86`) and default `"ＭＳ ゴシック"` vector search (`jz found` -> `jmp found`) alongside setting `lfFaceName = "Microsoft YaHei"` in `Hook_EnumFontFamiliesExA`, preventing empty `std::vector<LOGFONTA>` `_invalid_parameter_noinfo` (`0xc0000417`) crashes on CP936 Windows.
  - Documented hooking `GetACP`/`GetOEMCP` -> `932`, `GetLocaleInfoA` -> `0x0411`, `render.dll!D3DXCreateFontA`, and `MENUEX_TEMPLATE` (`ver=1`) + `DIALOGEX`/`DIALOG` via `FindResourceA`/`LoadResource`/`SizeofResource` (noting `imm32` `0x81` comparison for resource IDs `>= 128`).
- **Stripped-Ruby `.a0` `0xfe` Terminator Rule** ([`references/engine_and_patching.md`](./references/engine_and_patching.md)):
  - Clarified checking `b"\xff\xff" not in new_raw` (rebuilt chunk) so `.a0` `S` chunks whose original `\xff\xff` ruby tags were stripped by `VNTextPatch` always receive a trailing `0xfe`.

---

## [1.2.2] - 2026-11-22

### Added
- **CSystem Append Archive Extractor & Repacker** ([`scripts/csystem_append.py`](./scripts/csystem_append.py)):
  - Added `extract` and `pack` CLI subcommands powered by `CSystemArc.ArchiveReader` / `ArchiveWriter.WriteIndex()` via .NET reflection, extracting `.a0` scripts directly from `append.ini` (`.dat` + `.app`) archives without crashing on `type b0` delta images, and appending rebuilt `.a0` files idempotently back into `.app` / `.dat`.

### Changed & Fixed
- **Unified `sjis_ext.bin` & Complete-Export `winmm.dll` Proxy Hardening** ([`SKILL.md`](./SKILL.md), [`references/engine_and_patching.md`](./references/engine_and_patching.md)):
  - Documented staging both main-game (`Arc04.dat`) and append (`.app`) JSONs in one `VNTextPatch insertlocal` pass so `sjis_ext.bin` covers every CJK glyph across both campaigns.
  - Documented using a complete-export `winmm.dll` proxy (all 192 `SysWOW64\winmm.dll` exports, `codeCursor` aligned past `exports * 8`) when unpacked EXEs drop `VERSION.dll` or trigger AV false positives on `.patch` (`Trojan:Win32/Wacatac.B!ml`), avoiding `D3DXCreateFontIndirect FALSE` and `0xc0000142`.
  - Documented full Win32 `DIALOGEX` & common-control localization hooks (`TextOutA`, `SetDlgItemTextA`, `SendMessageA` for `WM_SETTEXT`/`CB_*`/`LB_*`/`TCM_INSERTITEMA`/`LVM_*`, plus `#32770`-filtered `ShowWindow` + `EnumChildWindows`), noting that omitting the `#32770` class filter causes severe main-window traversal lag whereas `4x` `GetGlyphOutlineW` itself only costs ~`142 ms` / 1,000 glyphs.

---

## [1.2.1] - 2026-11-20

### Added & Changed
- **Smart Batch Coalescing, Compact Payloads & Per-Batch Glossary Filtering** ([`scripts/translate_wave.py`](./scripts/translate_wave.py), [`scripts/api_translate_wave.py`](./scripts/api_translate_wave.py)):
  - Added compact `idx\tspeaker\tmessage` input encoding (eliminating repeated JSON keys/indentation and omitting already-mapped speaker names in output), per-batch glossary/speaker filtering (injecting only terms appearing in the current batch), and 240-line / 16k-char smart coalescing (`coalesce_max_lines=240` in `run_workflow`; `--pack-lines 240 --pack-chars 16000` in API worker) plus automatic primary TM pre-fill and post-wave propagation.
- **Natural SFX & H-Scene Sensual / Organ-Action Fidelity** ([`SKILL.md`](./SKILL.md), [`scripts/translate_wave.py`](./scripts/translate_wave.py), [`scripts/retry_blocked.py`](./scripts/retry_blocked.py)):
  - Refined translation instructions to render Japanese kana SFX into idiomatic Chinese sound textures/cadence and preserve authentic Galgame H-scene tension, anatomical terminology, and visceral organ actions/reactions without medical flattening.
- **Per-Line Refusal Leak Guard** ([`scripts/translate_wave.py`](./scripts/translate_wave.py), [`scripts/retry_blocked.py`](./scripts/retry_blocked.py)):
  - Added `REFUSAL_RE` verification across every returned item so refusal strings (`Unable to fulfill`, `Prohibited Use`, etc.) can never silently overwrite `chunks_out/*.json`.
- **Repository Hygiene** ([`.gitignore`](./.gitignore)):
  - Added `.gitignore` excluding `__pycache__/`, `*.py[cod]`, `*api_keys*`, and `*quota_state*.json`.

---

## [1.2.0] - 2026-11-20

### Added
- **Per-Key & Per-Project AI Studio Quota Manager** ([`scripts/key_quota_manager.py`](./scripts/key_quota_manager.py)):
  - Added persistent `key_quota_state.json` ledger storing only SHA-256 key fingerprints and masked prefixes (never persists raw secret API keys).
  - Enforces automatic daily quota rollover at **00:00 Pacific Time (`America/Los_Angeles`)** with countdown to local reset time.
  - Groups keys sharing a Google Cloud project (`project=<group>`) into a single shared quota pool so multi-key allocations under one project do not double-count free-tier limits.
  - Pre-flight 60s rolling **RPM** / **TPM** (accounting for `thinkingLevel=HIGH` thinking tokens) sliding-window pacing and least-used / weight-aware fair RPD allocation (`--reserve-rpd`, `--cap`, `--state-file` cross-project sharing).
  - Parses `429 RESOURCE_EXHAUSTED` JSON `QuotaFailure` (`PerDay` vs `PerMinute`) and `RetryInfo.retryDelay`, tracks `usageMetadata` (`promptTokenCount`, `candidatesTokenCount`, `thoughtsTokenCount`), and isolates invalid `400/403` keys cleanly.
  - Added CLI subcommands: `status`, `set-limit`, `plan`, `check-models` (zero `generateContent` RPD cost via `GET /v1beta/models`), and `reset`, plus `--plan-only` in `api_translate_wave.py`.

### Changed & Fixed
- **Save/Load Screen Mojibake Fix** ([`SKILL.md`](./SKILL.md), [`references/engine_and_patching.md`](./references/engine_and_patching.md)):
  - Documented the root cause when `VNTextProxy` (`msacm32.dll`) or dialogue-only hooks leave `USER32.dll!DrawTextA`, `USER32.dll!DrawTextExA`, and `GDI32.dll!ExtTextOutA` unhooked: Save/Load titles, timestamps, and `sjis_ext.bin` previews render as CP936 mojibake on Simplified Chinese Windows.
  - Added standard fix recipe: expand `.patch` section (`SizeOfRawData`, `SizeOfImage`, `CheckSum`), load `sjis_ext.bin` in `EntryStub` (skipping invalid trail bytes `0x09, 0x0A, 0x0D, 0x20, 0x2C`), and hook IAT `DrawTextA`/`DrawTextExA`/`ExtTextOutA` -> `sjis_ext.bin` + CP932 -> `*W`.
- **Bonus / Variant EXE & Third-Party Patch Safety**:
  - Added mandatory per-executable REA VA verification for bonus/fandisc EXEs (even when sharing identical engine DLLs like `render.dll` / `msacm32.dll`) and explicit guard against merging partial third-party `winmm.dll` proxy patches onto standalone portable builds.
- **Script & Ruby Audit Enhancements** ([`scripts/audit_scripts.py`](./scripts/audit_scripts.py)):
  - Added `argparse` support (`--dir`/`--project`, `--orig-dir`, `--portable-dir`) with automatic resolution of `work_<variant>` / `*_portable` layouts.
  - Added `[WARN]` detector for literal emphasis-dot / Japanese ruby annotations (`[・・・/...]`) left untranslated inside `gt_output/*.json`.
- **API Workers Integrated with QuotaManager** ([`scripts/api_translate_wave.py`](./scripts/api_translate_wave.py), [`scripts/api_retry_blocked.py`](./scripts/api_retry_blocked.py)):
  - Upgraded from ephemeral process-local cooldowns to persistent `QuotaManager` leases, token accounting, and post-wave quota summaries.

---

## [1.1.0] - 2026-11-16

### Added
- **13-Engine Universal Reference Matrix** ([`references/multi_engine_guide.md`](./references/multi_engine_guide.md)):
  - Added detection fingerprints, extraction, injection, encoding, and font/UI guides for `CSystem`, `Kirikiri 2/Z`, `Artemis`, `SiglusEngine/RealLive`, `BGI/Ethornell`, `CatSystem2`, `WillPlus/AdvHD`, `Qlie`, `YU-RIS`, `Majiro`, `Unity (Naninovel/Utage)`, `Ren'Py`, and `Electron/TyranoScript/RPG Maker`.
- **Recollection TM Deduplication & Blocked Recovery** ([`scripts/sync_recall_tm.py`](./scripts/sync_recall_tm.py)):
  - Added zero-API-cost exact-match translation memory sync for duplicate Recollection/Replay scripts, `--recover-blocked` cross-recovery for previously safety-blocked slices, and `--rechunk-todo 200` to optimize external daily RPD quotas.
- **External Multi-Key AI Studio Workers** ([`scripts/api_translate_wave.py`](./scripts/api_translate_wave.py), [`scripts/api_retry_blocked.py`](./scripts/api_retry_blocked.py)):
  - Added Google AI Studio REST API translation & layered retry workers supporting `thinkingLevel="HIGH"`, strict JSON schema output, multi-key rotation, `IncompleteRead`-safe `429` parsing, and socket retry handling.

### Changed & Fixed
- **Dynamic Win32 `DIALOGEX` Localization**: Documented in-place `.rdata` GBK patching for runtime `SendMessageA(CB_ADDSTRING / WM_SETTEXT)` controls (texture filters, GPU hardware info) while preserving `.patch` caption lookup strings.
- **Gallery/Manual `S` Slot Alignment & Whitespace Preservation**: Added handling for leading centering blank `\x81@\xfe` preservation and trailing empty `b"\xfe"` padding when `len(pat_s) < len(orig_s)`, plus preserving `\u3000` whitespace lines verbatim in translation workers.

---

## [1.0.0] - 2026-11-16

### Added
- Initial release of **`vn-localization`** skill ([`SKILL.md`](./SKILL.md), [`README.md`](./README.md), [`references/engine_and_patching.md`](./references/engine_and_patching.md)):
  - End-to-end CSystem + REA reverse engineering + GalTransl (`gt_input/` / `gt_output/` / categorized `glossary.json`) workflow.
  - Standalone registry-free & Locale-Emulator-free portable EXE patching (`.patch` section, `HKLM -> HKCU` redirect, `4x` supersampled `GetGlyphOutlineA` + `0.80` gamma LUT + `900 -> 400` outline weight clamp + Porter-Duff Over alpha compositing).
  - Adaptive `run_workflow` translation & layered safety-slice isolation (`translate_wave.py`, `retry_blocked.py`, `check_progress.py`).
  - Anti-loop `1:1` `M`-aligned `.a0` script reconstruction & unpacked binary verification (`audit_scripts.py`).
