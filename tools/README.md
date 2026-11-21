# tools/ — 工具层（库治理 · 取数校验 · 研究分析 · 对外导出）

> 事实锚点：2026-11-21 ｜ 模块数：根 13 个 `.py`（12 模块 + `__init__`）＋ 3 子包（db 12 / export 3 / research 7） ｜ 调用方式：`python main.py <命令>`，或 `python -m tools.<子包>.<模块>`

## 职责

- 承载「不进生产主链路、但必须可复跑」的操作：库体检/结构/断点/新鲜度、提交门禁、环境自检、冷备、覆盖对账、研究分析、清单导出。
- 根目录放跨子包的门禁与运维脚本；三个子包按领域分层（2026-11-05 W4，见 18 号 §330.2）：`db` 库治理、`export` 对外清单、`research` 研究分析。
- 工具不取数、不补数：缺口补拉仍走 `gap-update` / `backfill`。

## 模块清单

| 文件 | 作用 | 对外入口 |
|---|---|---|
| `gate.py` | 提交前四道门：ruff → `main.py --help` → `config-check` → `pytest`；`--fast` 跳过 pytest | `python tools/gate.py [--fast]` |
| `doctor.py` | 环境自检（依赖/路径/磁盘/断点/凭据/漏网接口/定时任务/事件面），默认离线 | `main.py doctor [--network / --json]` |
| `clean_scratch.py` | `_scratch/` 唯一清理入口，7 天窗、默认干跑、保护区精确名匹配 | `python tools/clean_scratch.py [--days N] [--apply]` |
| `data_backup.py` | 冷备清单/校验、硬链日备（7 版＋周备 4 版）、本地 tar.xz（不上传） | `main.py db backup`；`--make-manifest / --verify / --mirror` |
| `ckpt_tool.py` | 断点 `--show/--fix/--clear`，fix 幂等只增不减 | `main.py ckpt` → `run_ckpt()` |
| `quicklook.py` | `freshness` 核心表最新日期 / `peek` 尾部预览，不读全量 | `main.py freshness` / `peek` |
| `check_coverage.py` | 转债、ETF/LOF 当日覆盖与基金历史起点对账（R17/R18 同源） | `main.py check-coverage`；`check_fund_history()` |
| `consumer_coverage.py` | 决策侧消费标的逐只行数/起始日体检（511180 教训） | `main.py check-consumer` → `main()` |
| `consumer_pool.py` | `QIDIAN_POOL` / `XAU_WATCHLIST` / `WIND_CB_THERMOMETER` 冻结真源 | 仅 import |
| `config_check.py` | config 完整性八条判据（单一真源） | `main.py config-check`；`check_config()` |
| `regress_pipeline.py` | R1–R16 回归断言，全部只读或 dry-run | `main.py regress` → `run()` |
| `receipt_reconcile.py` | 回执对账：行数/唯一键/集合差/变更数/空值 | `python -m tools.receipt_reconcile` |
| `__init__.py` | 包声明 | 无 |

| 子包 | 模块数 | 覆盖 |
|---|---|---|
| `tools/db/` | 12 | 库体检、结构扫描、迁移、清理、断点、新鲜度、指纹（见 `db/README.md`） |
| `tools/export/` | 3 | ETF/LOF 分类统计与 Excel 导出（见 `export/README.md`） |
| `tools/research/` | 7 | 基金池/经理画像/因子/微盘/杠杆/ETF 资金流（见 `research/README.md`） |

## 根文件 vs 子包

- 根：被 CLI 直接挂载的入口型脚本（`gate` / `doctor` / `clean_scratch` / `data_backup` / `ckpt_tool` / `quicklook`），以及跨子包共用的校验件（`check_coverage` / `consumer_coverage` / `config_check` / `regress_pipeline` / `receipt_reconcile`）。
- 子包：按领域聚合——`db` 只碰库与断点，`export` 只产出文档/Excel，`research` 只读库做分析；子包内模块不反向依赖根目录脚本。
- `consumer_pool.py` 只提供冻结常量，被 `consumer_coverage.py` 导入，无独立 CLI。

## 新增工具落点判定

1. 改库/读库结构、断点、新鲜度、指纹 → `tools/db/`。
2. 从库生成对外交付文件（清单/Excel）→ `tools/export/`。
3. 基金/风险研究、口径计算 → `tools/research/`。
4. 跨领域门禁、环境、备份、覆盖对账 → `tools/` 根。
5. 落位后在 `cli/parser.py` 注册子命令（或注明仅 import 复用），否则 `regress` R1 不过。

## 与 CLI 的对应关系

| CLI | 实现 |
|---|---|
| `main.py regress` | `tools/regress_pipeline.py:run()` |
| `main.py doctor` | `tools/doctor.py:main()` |
| `main.py config-check` | `tools/config_check.py:main()`（`cli/misc_cmds.py`） |
| `main.py freshness` / `peek` | `tools/quicklook.py:run_freshness` / `run_peek` |
| `main.py ckpt` | `tools/ckpt_tool.py:run_ckpt()` |
| `main.py check-coverage` / `check-consumer` | `check_coverage.run()` / `consumer_coverage.main()` |
| `main.py db <子命令>` | `tools/db/*`（详见 `tools/db/README.md`） |
| `main.py lof-list` / `lof-export` | `tools/export/list_lof.py` / `export_lof.py` |
| `main.py fund-pool` / `manager-profile` / `micro-index` / `factor-lib` / `leverage` / `etf-flow` | `tools/research/*` |

## 红线

- `gate.py` 四道门任一失败即非零退出；`--fast` 只允许跳过 pytest，不得跳过前两道。
- `_scratch/` 只准 `clean_scratch.py` 清；其 `PROTECTED` 与 `_scratch/README.md` §三 必须成对维护。
- 凡写盘/写库命令默认干跑，`--apply` / `--execute` 才落盘（db clean、db migrate、meta-close、ckpt --fix、clean_scratch 同例）。
- `data_backup` 只做本地冷备、不上传；同盘镜像只防误删/写坏，不防盘坏。
- 路径一律取 `config` / `common.paths`，不硬编码盘符。
- 新增命令须同步 `cli/parser.py`；`regress` R1 校验注册完整性。
