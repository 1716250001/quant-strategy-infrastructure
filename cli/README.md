# cli/ — 主工程命令层（参数注册 + 分发 + 全部命令实现）

> 事实锚点：2026-11-21 ｜ 模块数：9 ｜ 入口：`main.py`（0.7 KB 薄壳）→ `cli.main()` → `cli.app.main()`

## 职责

- `main.py` 只保留兼容 re-export 与 `if __name__ == "__main__": main()`，命令实现全部在本包。
- `parser.py` 构建 argparse 注册表（实测 40 项：30 个可见命令 + 10 个隐藏平铺别名；`db` 下 13 个子命令）。
- 每个 `cmd_*` 是薄适配器：解析参数后**函数内延迟 import** `fetch`/`tools`/`alt`/`push` 并转接，不复制业务判断。

## 关键模块

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 仅导出 `build_parser` / `main`，作为包门面 |
| `app.py` | `main(argv=None)`：`setup_logging("chichao", "chichao.log")` → 建 parser → 分发 → `sys.exit(rc)`；`--json` 时不追加"完成。" |
| `parser.py` | `build_parser()` 与 `_EPILOG` 示例；注册全部命令；`_NoSuppressSubcommands` 让 `help=SUPPRESS` 的别名不出现在 `--help` |
| `_shared.py` | 公共参数小工具：`_add_date` / `_add_dry_run` / `_add_json`、`_banner`、`_today` |
| `fetch_cmds.py` | 数据拉取类 8 命令，转接 `fetch.daily_update` / `fund_nav_update` / `full_download` / `cb_download` / `backfill` / `schedule_runner` / `redtide_supply` |
| `db_cmds.py` | 库治理/速查命令与 `_DB_FAMILY` 定义（10 个 db 子命令 + 3 个专用子命令），转接 `tools.db.*` 与 `tools.ckpt_tool` |
| `alt_cmds.py` | alt 库 5 命令；`_alt_tier_choices()` 动态取 `alt.spec.TIERS`，不硬编码档位 |
| `research_cmds.py` | 研究与指数/基金 6 命令，转接 `tools.research.*` |
| `misc_cmds.py` | 覆盖体检、环境自检、推送测试、LOF 导出、`config-check`，转接 `tools.*` 与 `push.pushplus` |

## 命令族 → 模块

| 命令族 | 命令 | 实现模块 |
| --- | --- | --- |
| 数据拉取 | `gap-update` `intraday-update` `redtide-supply` `fund-nav` `download-full` `download-cb` `backfill` `schedule` | `fetch_cmds.py` |
| 库治理 / 速查 | `db {audit,schema,report,clean,migrate,freshness,scale-check,schema-align,fingerprint,ckpt,meta-close,backup,stale-fix}`、`regress` `freshness` `peek` | `db_cmds.py` |
| alt 库 | `alt-update` `alt-audit` `alt-backfill` `alt-us` `alt-verify` | `alt_cmds.py` |
| 研究 / 指数 / 基金 | `micro-index` `factor-lib` `fund-pool` `manager-profile` `leverage` `etf-flow` | `research_cmds.py` |
| 体检 / 推送 / 导出 | `check-coverage` `check-consumer` `doctor` `push-test` `lof-list` `lof-export` `config-check` | `misc_cmds.py` |
| 参数与分发 | 全部命令的参数定义 / 执行入口 / 公共参数 | `parser.py` / `app.py` / `_shared.py` |

隐藏别名（10 个）：`db-clean` `db-audit` `db-schema` `db-report` `db-migrate` `db-freshness` `db-scale-check` `db-schema-align` `db-fingerprint` `ckpt`，与对应 `db <sub>` 等价。

## 与其他包的关系

- 依赖方向：`cli` → `config`（`build_parser` 读 `__version__`）、`cli` → `common`（`app` 用 `logging_setup`）；业务包 `fetch`/`tools`/`alt`/`push` 仅在 `cmd_*` 函数体内延迟 import。
- 被依赖：仅 `main.py` 直接 import 本包；`tools/regress_pipeline.py` 经 `from main import build_parser` 与 `hasattr` 断言，间接受本包契约约束。
- 禁止反向依赖：`cli` 顶层不得 import `fetch`/`tools`/`alt`/`push`（避免启动开销与循环导入）；`fetch`/`tools`/`alt` 不得 import `cli`。

## 红线

- `main.py` 的 `build_parser` / `cmd_gap_update` / `cmd_leverage` / `cmd_etf_flow` re-export 被 `regress_pipeline` R5 断言依赖，禁删。
- 10 个隐藏别名保留注册（R1 契约依赖），只隐藏 `--help`，不摘出 choices。
- 写命令默认干跑：`--dry-run` 与 `--apply` 同时给出时按干跑执行（`db clean`/`db migrate`/`db backup` 等）。
- `--json` 模式 stdout 必须为纯 JSON，`app.main` 不追加完成语；`config-check` 用 `sys.exit` 传播非零退出码。
- 命令层不得复制业务判断：`cmd_schedule` 仅做 argparse→`fetch.schedule_runner.run()` 转接，`--refresh` 拒绝逻辑留在执行器。
- 档位/可选值不得硬编码（如 alt tier 动态取自 `alt.spec.TIERS`）。
