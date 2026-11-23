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
| `db_cmds.py` | 库治理/速查命令与 `_DB_FAMILY` 定义（10 个 db 子命令 + 4 个专用子命令：`meta-close`/`backup`/`stale-fix`/`health`），转接 `tools.db.*` 与 `tools.ckpt_tool` |
| `alt_cmds.py` | alt 库 5 命令；`_alt_tier_choices()` 动态取 `alt.spec.TIERS`，不硬编码档位 |
| `research_cmds.py` | 研究与指数/基金 6 命令，转接 `tools.research.*` |
| `misc_cmds.py` | 覆盖体检、环境自检、推送测试、LOF 导出、`config-check`，转接 `tools.*` 与 `push.pushplus` |

## 命令族 → 模块

| 命令族 | 命令 | 实现模块 |
| --- | --- | --- |
| 数据拉取 | `gap-update` `intraday-update` `redtide-supply` `fund-nav` `download-full` `download-cb` `backfill` `schedule` | `fetch_cmds.py` |
| 库治理 / 速查 | `db {audit,schema,report,clean,migrate,freshness,scale-check,schema-align,fingerprint,ckpt,meta-close,backup,stale-fix,health}`、`regress` `freshness` `peek` | `db_cmds.py` |
| alt 库 | `alt-update` `alt-audit` `alt-backfill` `alt-us` `alt-verify` | `alt_cmds.py` |
| 研究 / 指数 / 基金 | `micro-index` `factor-lib` `fund-pool` `manager-profile` `leverage` `etf-flow` | `research_cmds.py` |
| 体检 / 推送 / 导出 | `check-coverage` `check-consumer` `doctor` `push-test` `lof-list` `lof-export` `config-check` | `misc_cmds.py` |
| 参数与分发 | 全部命令的参数定义 / 执行入口 / 公共参数 | `parser.py` / `app.py` / `_shared.py` |

隐藏别名（10 个）：`db-clean` `db-audit` `db-schema` `db-report` `db-migrate` `db-freshness` `db-scale-check` `db-schema-align` `db-fingerprint` `ckpt`，与对应 `db <sub>` 等价。

可见命令 **30**（`--help` 分四组：数据维护 14 / 研究分析 11 / 推送导出 3 / 诊断 2）；`db` 子命令 **14**。

`db health`：默认轻量（doctor 摘要＋freshness＋audit/fingerprint 缓存，>7 天「过期」；
首次请 `--full` 建立缓存），`--full` 才跑全量 audit+fingerprint，`--strict` 任一不健康 ⇒ rc=1，`--json` stdout 单对象。

## 与其他包的关系

- 依赖方向：`cli` → `config`（`build_parser` 读 `__version__`）、`cli` → `common`（`app` 用 `logging_setup`）；业务包 `fetch`/`tools`/`alt`/`push` 仅在 `cmd_*` 函数体内延迟 import。
- 被依赖：仅 `main.py` 直接 import 本包；`tools/regress_pipeline.py` 经 `from main import build_parser` 与 `hasattr` 断言，间接受本包契约约束。
- 禁止反向依赖：`cli` 顶层不得 import `fetch`/`tools`/`alt`/`push`（避免启动开销与循环导入）；`fetch`/`tools`/`alt` 不得 import `cli`。

## 退出码契约（D2，2026-11-21）

| rc | 含义 | 典型来源 |
| --- | --- | --- |
| `0` | 成功（含显式 `source_empty` 且该表档位允许） | 全部业务步骤成功；只读命令未返回 `int`（`None` 按 0） |
| `1` | 受控业务失败（任一表 / 任一日 / 任一段失败） | `gap-update` `fail>0`；`redtide-supply` 子步骤异常；`fund-nav` 失败数>0；`download-*` / `backfill` 异常或返回失败；`alt-update` 任一表 `ok=False` 或 `errors` 非空 |
| `2` | 用法或配置错误 | argparse 参数缺失/非法；`schedule` 预算超限（`fetch/schedule_runner.run` 既有约定） |

- `app.main` 对 `None` 按 0 处理（只读命令兼容）；业务命令必须显式返回 `int`。
- 实例锁冲突固定归 `1`（`fetch/schedule_runner._InstanceLock` 以 `SystemExit` 退出）。
- `--json` 模式只约束 stdout 为纯 JSON，不改变退出码；失败提示写 stderr。
- **唯一例外**：`db scale-check --json PATH` 是文件输出（历史接口，保留不变）；其余 `db`
  命令的 `--json` 均为 stdout。新脚本优先用 `--out PATH` 落盘。
- 计划任务 bat 依赖 `if errorlevel 1`；业务失败必须落到 `1`，否则 `FAIL_MAIN` 分支不会触发。
- 已知缺口：`fetch/full_download.run_download`、`fetch/cb_download.run_cb_download`、`fetch/backfill.run_backfill` 的内部失败计数尚未返回，D2 只接通异常路径；`fund-nav` 的失败计数随 D4 的 `fund_nav_update` 改造提供，CLI 已支持 dict / 三元组两种透传形态。

## 红线

- `main.py` 的 `build_parser` / `cmd_gap_update` / `cmd_leverage` / `cmd_etf_flow` re-export 被 `regress_pipeline` R5 断言依赖，禁删。
- 10 个隐藏别名保留注册（R1 契约依赖），只隐藏 `--help`，不摘出 choices。
- 写命令默认干跑：`--apply` 与 `--execute` 同义（`db backup`/`db ckpt`/`db stale-fix`/`db meta-close`/`schedule`），`--dry-run` 始终优先（如 `db clean`/`db migrate`/`db backup`）。
- `--json` 模式 stdout 必须为纯 JSON，`app.main` 不追加完成语；`config-check` 用 `sys.exit` 传播非零退出码。
- 命令层不得复制业务判断：`cmd_schedule` 仅做 argparse→`fetch.schedule_runner.run()` 转接，`--refresh` 拒绝逻辑留在执行器。
- 档位/可选值不得硬编码（如 alt tier 动态取自 `alt.spec.TIERS`）。
