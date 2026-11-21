# fetch — 主库（tushare）数据采集与补数执行层

> 事实锚点：2026-11-21 ｜ 模块数：11 个生产模块（另有 2 个 `_legacy` 死代码）｜ CLI 入口：`python main.py gap-update`（另 `backfill` / `schedule` / `download-full` / `download-cb` / `fund-nav` / `redtide-supply` / `intraday-update`）

## 职责

- 负责主库 `market_data` 的取数：每日增量、周期事件面、全量建库、可转债、基金净值、赤潮派生、通用补数与周期调度。
- 提供共享基建：按接口限频 `RateLimiter`、断点 `Checkpoint`、带重试调用、线程池并发、无权限识别。
- 按规格层（该拉什么）/ IO 层（怎么取存）/ 执行层（怎么编排）拆分，统一落盘 `by_year` 布局。
- 不管 alt/wind 库；不做研究计算、体检判据与报告（由 `tools/*` 负责）。

## 关键模块

| 文件 | 作用 |
|---|---|
| `__init__.py` | 延迟导出 `get_pro` / `get_latest_trade_date` |
| `base.py` | 共享基建：`RateLimiter` / `Checkpoint` / `ts_call` / 并发拉取 / 权限拒绝记录 |
| `daily_update.py` | 每日增量统一入口：复用 backfill 的 by_date 路径 + 复核窗口 + market_state + 周期通道 |
| `periodic_update.py` | `dividend`/`namechange` 周期增量：状态文件到期判定、窗口 ∩ lookback、按主键幂等落盘 |
| `full_download.py` | 个股/ETF/LOF/财务全量下载，元数据覆盖守卫 `_guard_overwrite` |
| `cb_download.py` | 可转债 `cb_basic`/`cb_daily` 全量下载，2000 行截断续拉 |
| `fund_nav_update.py` | `fund_nav` 按 `nav_date` 回溯增量 + `repair_incomplete` 历史回补 |
| `redtide_supply.py` | 赤潮 `market_state` 跌停派生；`adj_factor`/`stk_limit` 已转发 daily_update |
| `backfill_spec.py` | 任务规格层：`resolve_codes` / `build_tasks` / `estimate_requests`（纯计算） |
| `backfill_io.py` | IO 层：按日分页拉取、三种落盘布局、前置基础表 `ensure_meta` |
| `backfill.py` | 补数执行编排 + CLI，五种取数模式与断点续跑 |
| `schedule_runner.py` | 按 `config.schedule` 频率筛表，复用 `run_target`，预算护栏 + 实例文件锁 |
| `_legacy/gap_update.py` | 废弃逐文件更新引擎（受回归 R7 保护，禁止调用） |
| `_legacy/intraday_update.py` | 废弃尾盘更新引擎（同上，禁止调用） |
| `_legacy/__init__.py` | 旧链路目录分层标记 |

## 与其他包的关系

- 依赖 `config/*` 与 `common/*`；`cli/fetch_cmds.py` → `main.py` 子命令 → 本包 `run_*` / `main()`。
- `daily_update` 与 `schedule_runner` 复用 `backfill.run_target`；其余下载器共用 `fetch.base`。
- `_legacy` 仅被回归 R7 import，用于断言废弃引擎抛 `DeprecatedEngineError`。
- 禁止反向依赖：fetch 不 import alt/wind；`tools` 可调用 fetch，fetch 不 import `tools`。

## 红线

- 限频按接口独立档位（daily 实测 300 次/分，其余见 `API_RATE_LIMIT`），并发默认 4 线程/接口，限频走 1~3 秒短退避。
- 分页防静默截断：`fut_holding`/`opt_daily` 等必须 `limit/offset` 翻页（实测丢 62%/42%）。
- 断点三态：失败不记；成功空 DataFrame 记断点；`None`（调用失败）与空 DataFrame 必须区分。
- `by_date` 必须按今天截断（交易日历含未来交易日）；每日更新强制复核最近 `DAILY_UPDATE_LOOKBACK` 个交易日。
- 存储布局单一真源 `config.TABLE_DATE_COL`，`mode`（怎么取）≠ `layout`（怎么存）；`_legacy` 两模块禁止在流水线与定时任务中调用。
- 元数据覆盖前过 `_guard_overwrite`：新行数少于现有行数即拒绝覆盖。
