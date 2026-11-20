# Changelog / 版本迭代记录

All notable changes to the **vn-localization** skill are documented in this file.

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
