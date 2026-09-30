# 代码\ — 目录说明

> **位置**：`D:\量化策略\代码\`
> **身份**：**本目录就是 Git 仓库根**（`quant-strategy`，远端 `git@github.com:1716250001/quant-strategy.git`）
> **版本**：主工程 `5.0.0`（`config.py` 单一真源）｜回测工具 `btf 1.1.0`
> **不在本目录**：数据（`..\数据\`）、回测产物（`..\回测产物\`）、事实类文档（`..\文档\`）——见 §6
> **顶层总览**：`..\README.md`（工作区级的目录树、数据红线、变更记录）

---

## 1. 本目录是什么

A股三层轮动金字塔策略的**全部代码 + 架构文档 + 自带运行环境**。两个工程共存：

| 工程 | 入口 | 版本 | 说明 |
|---|---|---|---|
| **主工程**（本层） | `python main.py <cmd>`（安装后亦可 `pyramid <cmd>`） | 5.0.0 | 数据维护 / 全量库体检 / 因子与指数分析 / 奇点扫描 / 推送。**36 个命令名**（30 可见顶级 + 6 隐藏兼容别名，`db` 命令族另含 6 个子命令） |
| **回测工具子系统** | `btf\`（`bt <cmd>`） | btf 1.1.0 | 独立工程（自带 `pyproject.toml` / `CHANGELOG.md` / 九项质量门）；**11 条命令族** + `--version` |

两者的 venv 是同一个：`代码\_venv\Scripts\python.exe`。

---

## 2. 目录结构（顶层）

```
代码\                              ← 本目录 = Git 仓库根
├─ main.py                        统一 CLI 入口（子命令注册表；v5.1 起新增命令只需注册一个函数）
├─ config.py                      ★ 全局配置中心（115 KB）：路径 / 凭据引用 / 标的清单 / 计算参数
│                                   `__version__` 为工程版本唯一真源（pyproject 走 dynamic 读取）
├─ config_alt.py                  alt 库（akshare 另类/海外数据）配置
├─ indicators.py                  技术指标库（KDJ / CCI / MACD / RSI / BOLL / BBI…）
├─ requirements.txt               ★ 依赖清单（部署视图：28 包全量冻结）
├─ pyproject.toml                 工程配置（开发视图：8 个直接依赖；[project.scripts] pyramid 短命令）
├─ .env / .env.example            凭据（Tushare token / PushPlus…）；`.env` **永不进仓**
├─ .gitignore                     忽略规则：凭据 / 环境 / 暂存归档 / 非代码产物
│
├─ common\                        跨模块公共层（路径 / IO / 日历 / 代码 的唯一真源，8 模块）
├─ fetch\                         数据拉取类任务（12 模块：增量、全量、可转债、基金净值、通用补数）
├─ strategies\                    策略信号层（qidian 奇点战法）
├─ tools\                         运维/分析脚本 22 个（数据库体检、体检报告、因子库、基金池、杠杆监控…）
├─ push\                          推送（PushPlus 微信推送）
├─ alt\                           alt 库（物理隔离于主库；akshare 源，11 模块）
├─ 代码架构\                       ★ 架构文档体系（40 份，2026-09-30 自 文档\ 迁入）——见 §5
│
├─ btf\                           ★ 回测工具子系统（自成工程）——见 §4
│
├─ _python\                       Python 3.13.15 解释器（约 61 MB，不入库）
├─ _venv\                         虚拟环境（约 530 MB，不入库）
├─ _scratch\                      临时脚本区（对话/日任务产物，可随时清；不入库）
├─ _archive\                      归档区（停用脚本/一次性工具，7 个批次 + INDEX.md；不入库）
└─ pyramid_strategy.egg-info\     `pip install -e .` 产物（不入库）
```

---

## 3. 主工程（本层）

### 3.1 核心文件

| 文件 | 职责 |
|---|---|
| `main.py` | 统一 CLI。命令族见 §8；注册表与帮助文本同源，命令计数与 `build_parser()` 保持一致 |
| `config.py` | 唯一配置中心：**所有路径**（`PROJECT_DIR` / `DATA_DIR` / `DOC_DIR` …）、凭据引用、标的清单、计算参数。一处修改全局生效 |
| `indicators.py` | 技术指标库（统一 BBI 短/长周期 + MACD DataFrame 版） |
| `config_alt.py` | alt 库配置（P0~P4 分级、URL、限频） |

### 3.2 分包

| 包 | 职责 | 关键模块 |
|---|---|---|
| `common\` | 公共层：**路径 / IO / 日历 / 代码 的唯一真源** | `paths.py`（路径解析唯入口，禁止 `__file__` 推算）、`parquet_store.py`（增量合并 / 按年分区 / 批写）、`reader.py`、`calendar.py`、`codes.py`、`jsonio.py`、`logging_setup.py` |
| `fetch\` | 拉取类任务 | `daily_update.py`（★ 每日增量统一入口）、`full_download.py`（建库/重建，断点续跑）、`cb_download.py`、`fund_nav_update.py`、`redtide_supply.py`、`backfill{,_io,_spec}.py`（通用补数三件套）、`base.py`（限频/断点/并发基建） |
| `strategies\` | 策略信号层 | `qidian.py`（标的池/取数/指标/信号）、`qidian_daily_scan.py`（扫描编排） |
| `tools\` | 运维与分析脚本（被 CLI 调用，也可单独跑） | `db_{audit,schema,clean,migrate}.py`、`ckpt_tool.py`、`doctor.py`、`config_check.py`、`check_coverage.py`、`consumer_coverage.py`、`regress_pipeline.py`、`build_micro_index.py`、`factor_library.py`、`fund_pool_builder.py`、`manager_profile.py`、`leverage_monitor.py`、`etf_flow.py`、`{list,export}_lof.py`、`gen_db_report.py`、`quicklook.py`、`clean_scratch.py`、`utils.py` |
| `push\` | 推送 | `pushplus.py` |
| `alt\` | alt 库（另类/海外数据，**物理隔离**于主库） | `update.py`、`audit.py`、`backfill.py`、`us_backfill.py`、`verify_spec.py`、`reader.py`、`spec.py`、`rate.py`、`urls.py`、`io.py` |

### 3.3 依赖与环境

- **双文件口径（刻意并存）**：`requirements.txt` = 部署视图（28 包全量冻结，重建环境用它）；`pyproject.toml` = 开发视图（8 个直接依赖，说明代码引用面）。版本冲突时以 `requirements.txt`（冻结实测）为准。
- **环境自带**：`_python\`（解释器）+ `_venv\`（40 包）。一切命令显式走 `_venv\Scripts\python.exe`，不依赖系统环境。
- **短命令**：`pip install -e .` 后可用 `pyramid <cmd>`；旧写法 `python main.py <cmd>` 保持兼容。

---

## 4. `btf\` — 回测工具子系统

独立工程（自带 pyproject / CHANGELOG / 门禁），**版本 1.1.0**（`btf\_version.py` 单一真源）。

```
btf\
├─ pyproject.toml      ruff + import-linter 十契约 + pytest 五层标记（l1–l5 / smoke）
├─ requirements.txt    依赖预算 ≤8（架构 07 §12.11）
├─ CHANGELOG.md        完整版本留痕（Keep a Changelog 结构）
├─ btf\                17 个包：domain / data / strategy / engine / execution / risk /
│                      portfolio / analytics / experiment / optimize / runtime / app /
│                      viz / registry / config / cli（+ btf_datasets\ 黄金集）
├─ schemas\            配置 JSON Schema（backtest.v1.json）
├─ benchmarks\         B1–B5 基准留档
├─ examples\           示例策略（rotation.py）+ 配置
├─ tools\              常驻工具 16 个（九项门禁脚本 + 对账/复跑/冷备/预算）+ probes\ 探针
└─ tests\              890 项（unit / integration / regression / system / benchmark / perf / fixtures）
```

- **命令族 11 条**：`config-check` / `run` / `report` / `verify` / `test` / `dataset` / `optimize` + `check` / `cold-backup` / `runs` / `show`（另有 `bt --version`）
- **质量门 `bt check` 9/9**：ruff · import-linter 十契约 · 插件边界（SHA256 基线）· domain 白名单 · 依赖预算 8/8 · 冒烟 · 报告渲染级断言 · 数据不变量（含负向对照）· IO 单一入口（AST 扫描 + 变异性自检）
- 纪律示例：随机数一律经 `ctx` 已播种 Generator；`data.core` 是唯一触磁盘模块；一切改动须过门禁 + 追加 CHANGELOG

---

## 5. `代码架构\` — 架构文档体系

2026-09-30 自 `..\文档\` 整体迁入（与代码同仓，便于版本管理与就近引用）。

| 子目录 | 内容 |
|---|---|
| `回测工具架构\` | **22 份**：01–17 号架构基线（v0.3 冻结，五轮评审闭环）+ 18 号分步编码计划（执行状态跟踪）+ 19 号架构审查与重构方案 + README（导航/映射表） |
| `全量数据库架构\` | **18 份**：MD-Foundation（mdf）v0.1 设计稿 + README |

改 btf 前先读 `回测工具架构\README.md` 的阅读路径；任务级变更按 18 号的「使用约定」登记。

---

## 6. 数据与产物（不在本目录）

| 位置 | 内容 |
|---|---|
| `..\数据\` | 全量数据库（`daily_data\`，Parquet 按年分区）+ `logs\` + `signals\` |
| `..\回测产物\` | btf 回测 run 目录与验收报告 |
| `..\文档\` | 事实类文档：`数据与工程\` / `策略体系\` / `市场研究\` / `舰队福利提取\` |
| `..\备份\` | 冷备与归档（含跨版本源码冷备 `*-cold\`） |

**路径唯一真源**：`config.py` → `common\paths.py`。禁止在脚本里用 `__file__ + ".."` 自行推算，禁止硬编码。

---

## 7. 临时区与归档区（不入库）

| 目录 | 说明 |
|---|---|
| `_scratch\` | 临时脚本与运行残留（pytest 临时 run、一次性实验）。可随时清。含 `wind-研究暂存\`（Wind 研究暂停 2026-09-25） |
| `_archive\` | 归档区，**有 `INDEX.md`**：`legacy链-20260925` / `日报链路-20260924` / `背离扫描器-20260924` / `信号有效性验证-20260924` / `迁移工具-20260925` / `wind-probe-验证脚本-20260925` / `temp-20260923`。**注意含历史明文凭据，勿外传** |
| `__pycache__\` / `*.egg-info\` | 构建产物 |

`.gitignore` 覆盖：`.env`（凭据）· `_python/` `_venv/` · `_scratch/` `_archive/` · `*.parquet` `*.log` `*.bak*` · `回测产物/`。

---

## 8. 常用命令

```powershell
# 环境（解释器与 venv 均在本目录内）
D:\量化策略\代码\_venv\Scripts\python.exe main.py doctor      # 环境自检（依赖/路径/磁盘/断点/凭据）

# 数据维护（盘后）
python main.py gap-update          # 差额补全（库最新日 → 今天）
python main.py intraday-update     # 尾盘增量（= gap-update --lookback 1）
python main.py redtide-supply      # 赤潮数据补全（adj_factor / stk_limit / market_state）
python main.py fund-nav            # 基金净值增量
python main.py download-full       # 全市场建库/重建（断点续跑）
python main.py check-coverage      # 覆盖体检（可转债 + ETF/LOF）
python main.py check-consumer      # 决策侧消费标的覆盖体检

# 库维护与体检
python main.py db audit            # 全量库体检（断点/重复/缺口/规模）
python main.py db schema           # 结构扫描（机器可读 schema JSON）
python main.py db report           # 结构报告 Markdown（--refresh 先扫描）
python main.py db clean            # 存量清理（默认干跑 + 自动备份）
python main.py db migrate          # 存储布局迁移（by_code → by_year）
python main.py db ckpt             # 断点管理
python main.py config-check        # config.py 完整性检查
python main.py regress             # 回归测试（盘后流水线 + 存储布局约束）

# 速查与分析
python main.py freshness / peek    # 数据新鲜度 / 数据预览
python main.py qidian              # 奇点战法每日双重信号扫描
python main.py micro-index         # 本地自建微盘指数
python main.py factor-lib          # 因子库概览
python main.py leverage / etf-flow # 杠杆风险监控 / ETF 份额资金流

# alt 库
python main.py alt-update / alt-audit / alt-backfill / alt-us / alt-verify

# 回测工具（btf；短命令 bt / pyramid 已装在本 venv\Scripts\ 下）
cd btf
..\_venv\Scripts\python.exe -m pytest           # 全量测试（890 项）
..\_venv\Scripts\python.exe tools\check.py      # 等价于 bt check（9/9 门禁）
bt run -c <config> / bt report / bt verify / bt check   # 跑回测 / 报告 / 复核 / 门禁
```

---

## 9. 红线与约定

1. **凭据只在 `.env`**，永不进仓；`_archive\` 含历史明文凭据，勿外传。
2. **所有路径从 `common\paths.py` 取**——禁自行推算 `__file__`、禁硬编码、禁多层 fallback。
3. **改动生产流水线前先 `--dry-run`**；数据表清单/结构改动后跑 `config-check`。
4. **回测工具改动须过 `bt check`（9/9）**，并在 `btf\CHANGELOG.md` 追加条目。
5. **任务级变更须登记**：`代码架构\回测工具架构\18-分步编码计划.md` + `..\README.md` §八。
6. **验证话术纪律**：不以"全绿"作结论，须附实测输出摘要（btf 铁律）。

---

## 10. 相关文档

| 文档 | 位置 |
|---|---|
| 工作区总览 / 数据红线 / 变更记录 | `..\README.md` |
| btf 架构基线（01–19 号） | `代码架构\回测工具架构\README.md` |
| 全量数据库设计（MD-Foundation） | `代码架构\全量数据库架构\README.md` |
| 数据库事实说明（表结构/读取方法/权限） | `..\文档\数据与工程\` |
| 策略体系与研究 | `..\文档\策略体系\` · `..\文档\市场研究\` |
