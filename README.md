# vn-localization

一套面向 **Antigravity Agent** 的视觉小说 / Galgame 端到端汉化与本地化工作流技能（Skill），覆盖从**引擎识别、脚本提取、GalTransl 工程化、独立便携化 EXE 改造（去注册表 / 去 LE）、中文字体高质量渲染**到**实时用量自适应分批翻译、防死循环脚本核对与重打包发布**的完整闭环。

## 核心特性

1. **引擎识别与 REA 逆向定位**
   - 结合 `reverse-engineer-anything`（REA）MCP 与封包指纹（如 CSystem `Arc00~06.dat` + `config.dat` + `render.dll`）定位 Archive 版本、安装检测、区域编码检查与字体渲染调用链。
2. **独立便携化 EXE 改造（免注册表 / 免 Locale Emulator）**
   - 将补丁代码直接固化至新增 `.patch` 节区（清除 `DYNAMIC_BASE`、校验原字节、重算 PE CheckSum），不依赖外置注入器或临时代理 DLL。
   - 绕过安装校验与重定向 `HKLM` 注册表读取，Hook `GetUserDefaultUILanguage` / `GetCPInfo`（配合 `sjis_ext.bin`）及 `*A -> *W` 界面 API；并排查 Unicode `DIALOGEX` 中由 `SendMessageA(CB_ADDSTRING / WM_SETTEXT)` 动态填入的 `.rdata` CP932 字串（如「全屏时纹理过滤设置」下拉框与「显卡设备信息」框），在不破坏 `.patch` 匹配表的前提下原地替换为简中 GBK，无需管理员权限或 LE 即可无乱码运行。
3. **高清简体中文字体渲染优化**
   - 统一替换为 `Microsoft YaHei`（`GB2312_CHARSET`），归一化负 Em 字高并将 `FW_HEAVY (900)` 描边字重钳制为 `400`。
   - 对 `GetGlyphOutlineA` 实施 **`4x` 超采样光栅化 + `0.80` Gamma LUT** 修复小中号密集笔画（如「算」「管」）的 Hinting 粗细跳变，并注入 **Porter-Duff Over 平滑 Alpha 混合** 消除硬边缘与灰边。
4. **GalTransl 兼容结构与固定译名规范**
   - 使用 `CSystemArc.exe` 与 `VNTextPatch.exe` 提取为标准 `gt_input/` / `gt_output/` 结构，切分 `chunks_in/`（100 行/块）+ `manifest.json`。
   - 通过 `glossary.json` 强制维护 `name_map` 与四类固定词表：**【人物名称/称呼】**、**【道具/物品】**、**【地点/机构】**、**【其他专有名词/术法/种族】**。
5. **自适应分批翻译、回想同步与分层拒翻回收（`run_workflow` / TM 交叉找回）**
   - **固定高水准模型**：默认强制使用 `model="inherit"` 并遵循 `humanizer-zh` 润色规则，消除机翻腔与多余译制结构，并完整保留 `\u3000` 等纯空白格式行。
   - **实时用量、RPD 配额优化与防错**：根据每批耗时、吞吐与 `429` 限流动态调整 `batch_size` 与并发窗口（`usage_state.json`），遇配额不足立即熔断通知；若使用受日请求数限制（RPD）的外部 Free API Key，可将未翻连续块合并扩充至 **`≤200` 行/块**、不把额度浪费在空探测上，并在轮换 Key 时区分 `PerDay` 与瞬时 `429`、兜底处理 `429` 响应体的 `IncompleteRead`。
   - **回想副本免 API 同步与拒翻零残留（`sync_recall_tm.py`）**：优先翻译正篇剧本后，用精确译文记忆自动回填回想副本（大幅削减重复请求，保证回想模式同步汉化）；整块拒翻先写入 `skipped_chunks.json`，经 `sub_size=20 -> 10 -> 5` 逐层剥离，再结合 `--recover-blocked` 与回想副本交叉找回，实现极小残留甚至 `100%` 全清零。
   - **强制进度汇报**：每次翻译或重试批次结束后自动输出分块、文件覆盖、行数百分比与拦截统计。
6. **原脚本 1:1 防死循环重打包与自动核对（`audit_scripts.py`）**
   - 解决 `VNTextPatch insertlocal` 默认折行拆分 `S` 记录、剥离 `0xfe` 结束符及误改未翻译脚本导致的跳转索引偏移（如 `# N(100)` / `# N(1)` -> `<4c> N(0)` 向后退跳转形成死循环），并兼容特典/说明脚本中前导空格 `\x81@\xfe` 与自动折行合并导致的 `S` 块数缩减（保留前导空行占位与尾部补空 `b"\xfe"`）。
   - 重打包时按原版 `M` 指令流保持 **1:1 chunk 索引与 `0xfe` 规则**，并通过 `audit_scripts.py` 自动解包 `Arc01.dat` / `Arc04.dat` 核对全部分卷脚本与 JSON 质量。

## 目录结构

```text
vn-localization/
├── SKILL.md                         # 技能主入口、核心不变量与 6 步工作流说明
├── README.md                        # 仓库说明文档与版本概要
├── CHANGELOG.md                     # 版本迭代记录（v1.0.0 -> v1.2.0）
├── references/
│   ├── multi_engine_guide.md        # 13 类主流 Galgame 引擎解包/注入/字体/UI 通用对照表
│   └── engine_and_patching.md       # 工具参数、GalTransl 词表规范、PE/字体/存档/对话框 Hook 与防死循环打包细节
└── scripts/
    ├── translate_wave.py            # 实时用量自适应波次并行翻译脚本 (run_workflow)
    ├── retry_blocked.py             # 拒翻分块分层切片重试与安全隔离脚本 (run_workflow)
    ├── key_quota_manager.py         # AI Studio 单 Key/项目额度分配、60s 滑窗限流与 PT 零点重置管理器
    ├── api_translate_wave.py        # Google AI Studio 多 Key 配额调度翻译脚本（支持 PerDay/RPM 区分与 HIGH 思考）
    ├── api_retry_blocked.py         # 配合 AI Studio REST API 与配额管理器的分层切片重试脚本
    ├── sync_recall_tm.py            # 回想副本精确译文记忆同步与拒翻片段交叉找回工具
    ├── check_progress.py            # 翻译进度统计脚本（分块/文件/行数/拦截统计）
    └── audit_scripts.py             # GalTransl JSON 与解包 .a0 二进制脚本 1:1 自动化核对工具
```

## 版本迭代概要（详见 [CHANGELOG.md](./CHANGELOG.md)）

- **v1.2.0 (2026-11-20)**：新增 AI Studio 逐 Key / 按 Project 共享池额度管理器 `key_quota_manager.py`（`00:00 PT` 自动清零、60s RPM/TPM 滑窗、`--reserve`/`--cap`、零损耗 `check-models` 与分块预分配 `plan`）；补充 Save/Load 存档屏 `DrawTextA`/`DrawTextExA`/`ExtTextOutA` + `sjis_ext.bin` 解码防乱码方案、特典衍生版 EXE VA 复核与第三方补丁冲突规范；升级 `audit_scripts.py` 对 `work_*` 变体与 `[・・・/...]` 着重号注音残留的检测。
- **v1.1.0 (2026-11-16)**：扩展至 13 类主流 VN 引擎通用指南（`multi_engine_guide.md`），新增回想副本免 API 同步/交叉找回（`sync_recall_tm.py`）与 AI Studio 多 Key REST 翻译/切片工具，完善 Win32 `DIALOGEX` 动态控件 GBK 修复与 `\u3000` 空行保留。
- **v1.0.0 (2026-11-16)**：初始发布完整 CSystem + REA + 独立去注册表/去 LE 便携版 + 4x 超采样字体渲染 + `run_workflow` 分层拒翻剥离 + `1:1` 防死循环核对闭环。

## 快速使用

在您的项目目录中将该仓库放置到 `.agents/skills/vn-localization/`（或全局 Agent 技能目录）下即可被 Antigravity 自动识别与调用：

```powershell
# 查看当前翻译进度
python ".agents/skills/vn-localization/scripts/check_progress.py" "<game_root>/work/gt_project"

# 可选：使用外置 AI Studio 多 Key 轮询翻译（Key 保存在本地文件或 GEMINI_API_KEYS 环境变量中，切勿提交到仓库）
python -u ".agents/skills/vn-localization/scripts/api_translate_wave.py" --project "<game_root>/work/gt_project" --keys-file "<path/to/api_keys.txt>"

# 翻译并重打包后，自动核对 JSON 与解包 .dat 脚本完整性
python ".agents/skills/vn-localization/scripts/audit_scripts.py" "<game_root>"
```
