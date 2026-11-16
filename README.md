# vn-localization

一套面向 **Antigravity Agent** 的视觉小说 / Galgame 端到端汉化与本地化工作流技能（Skill），覆盖从**引擎识别、脚本提取、GalTransl 工程化、独立便携化 EXE 改造（去注册表 / 去 LE）、中文字体高质量渲染**到**实时用量自适应分批翻译、防死循环脚本核对与重打包发布**的完整闭环。

## 核心特性

1. **引擎识别与 REA 逆向定位**
   - 结合 `reverse-engineer-anything`（REA）MCP 与封包指纹（如 CSystem `Arc00~06.dat` + `config.dat` + `render.dll`）定位 Archive 版本、安装检测、区域编码检查与字体渲染调用链。
2. **独立便携化 EXE 改造（免注册表 / 免 Locale Emulator）**
   - 将补丁代码直接固化至新增 `.patch` 节区（清除 `DYNAMIC_BASE`、校验原字节、重算 PE CheckSum），不依赖外置注入器或临时代理 DLL。
   - 绕过安装校验与重定向 `HKLM` 注册表读取，Hook `GetUserDefaultUILanguage` / `GetCPInfo`（配合 `sjis_ext.bin`）及 `*A -> *W` 界面 API，无需管理员权限或 LE 即可在简体中文 Windows 直接运行。
3. **高清简体中文字体渲染优化**
   - 统一替换为 `Microsoft YaHei`（`GB2312_CHARSET`），归一化负 Em 字高并将 `FW_HEAVY (900)` 描边字重钳制为 `400`。
   - 对 `GetGlyphOutlineA` 实施 **`4x` 超采样光栅化 + `0.80` Gamma LUT** 修复小中号密集笔画（如「算」「管」）的 Hinting 粗细跳变，并注入 **Porter-Duff Over 平滑 Alpha 混合** 消除硬边缘与灰边。
4. **GalTransl 兼容结构与固定译名规范**
   - 使用 `CSystemArc.exe` 与 `VNTextPatch.exe` 提取为标准 `gt_input/` / `gt_output/` 结构，切分 `chunks_in/`（100 行/块）+ `manifest.json`。
   - 通过 `glossary.json` 强制维护 `name_map` 与四类固定词表：**【人物名称/称呼】**、**【道具/物品】**、**【地点/机构】**、**【其他专有名词/术法/种族】**。
5. **自适应分批翻译与分层拒翻回收（`run_workflow`）**
   - **固定高水准模型**：默认强制使用 `model="inherit"` 并遵循 `humanizer-zh` 润色规则，消除机翻腔与多余译制结构。
   - **实时用量与配额自适应（`usage_state.json`）**：根据每批耗时、吞吐与 `429` 限流动态调整 `batch_size` 与并发窗口；遇可用额度/配额不足时立即触发 `QUOTA_INSUFFICIENT` 安全熔断并保留断点通知用户。
   - **分层切片重试**：整块拒翻先写入 `skipped_chunks.json`，再经 `sub_size=20 -> 10 -> 5` 逐层剥离，仅将真正无法通过的最小片段以日文原文保留并记录到 `safety_blocked_segments.{json,txt}`。
   - **强制进度汇报**：每次翻译或重试批次结束后自动输出分块、文件覆盖、行数百分比与拦截统计。
6. **原脚本 1:1 防死循环重打包与自动核对（`audit_scripts.py`）**
   - 解决 `VNTextPatch insertlocal` 默认折行拆分 `S` 记录、剥离 `0xfe` 结束符及误改未翻译脚本导致的跳转索引偏移（如 `# N(100)` / `# N(1)` -> `<4c> N(0)` 向后退跳转形成死循环）。
   - 重打包时按原版 `M` 指令流保持 **1:1 chunk 索引与 `0xfe` 规则**，并通过 `audit_scripts.py` 自动解包 `Arc01.dat` / `Arc04.dat` 核对全部分卷脚本与 JSON 质量。

## 目录结构

```text
vn-localization/
├── SKILL.md                         # 技能主入口、核心不变量与 6 步工作流说明
├── README.md                        # 仓库说明文档
├── references/
│   └── engine_and_patching.md       # 工具参数、GalTransl 词表规范、PE/字体 Hook 与防死循环打包细节
└── scripts/
    ├── translate_wave.py            # 实时用量自适应波次并行翻译脚本 (run_workflow)
    ├── retry_blocked.py             # 拒翻分块分层切片重试与安全隔离脚本 (run_workflow)
    ├── check_progress.py            # 翻译进度统计脚本（分块/文件/行数/拦截统计）
    └── audit_scripts.py             # GalTransl JSON 与解包 .a0 二进制脚本 1:1 自动化核对工具
```

## 快速使用

在您的项目目录中将该仓库放置到 `.agents/skills/vn-localization/`（或全局 Agent 技能目录）下即可被 Antigravity 自动识别与调用：

```powershell
# 查看当前翻译进度
python ".agents/skills/vn-localization/scripts/check_progress.py" "<game_root>/work/gt_project"

# 翻译并重打包后，自动核对 JSON 与解包 .dat 脚本完整性
python ".agents/skills/vn-localization/scripts/audit_scripts.py" "<game_root>"
```
