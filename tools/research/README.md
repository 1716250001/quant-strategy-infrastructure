# tools/research — 研究分析子包（基金池 · 经理画像 · 因子 · 微盘 · 杠杆 · 资金流）

> 事实锚点：2026-11-21 ｜ 模块数：7 ｜ 调用方式：`python main.py <命令>`，或 `python -m tools.research.<模块>`

## 职责

- 只读主库做基金与风险维度分析，产出可引用的证据或本地派生序列；不接决策、不写库。
- 基金侧：主动权益池、经理风格画像、全收益因子库、自建微盘指数、source_empty 注册表对账。
- 风险侧：B 口径杠杆热度、宽基 ETF 份额与申赎资金流。

## 模块清单

| 文件 | 作用 | 对外入口 |
|---|---|---|
| `fund_stale.py` | 基金 source_empty 注册表对账，判定复用 `check_fund_history` | `db stale-fix` → `detect()`/`apply_fix()` |
| `fund_pool_builder.py` | 主动权益基金池九步口径（32,777 → 2,336） | `main.py fund-pool` → `build()` |
| `manager_profile.py` | 经理风格画像：像谁、跟踪指数、β | `main.py manager-profile` → `profile()` |
| `build_micro_index.py` | 本地微盘指数（每日总市值最小 400 只等权，T-1 定成分） | `main.py micro-index` → `build()` |
| `factor_library.py` | 全收益基准＋风格因子加载与 OLS 回归 | `main.py factor-lib` → `load_factors()` |
| `leverage_monitor.py` | B 口径杠杆热度＋黄/红警戒（只风险维度） | `main.py leverage` → `get_leverage_state()` |
| `etf_flow.py` | 7 只宽基 ETF 份额变化与 5/20 日申赎估算 | `main.py etf-flow` → `main()` |

## 与 CLI 的对应关系

| CLI | 实现 |
|---|---|
| `main.py db stale-fix` | `tools/research/fund_stale.py`（经 `cli/db_cmds.cmd_db_stale_fix`） |
| `main.py fund-pool` | `tools/research/fund_pool_builder.py:main()` |
| `main.py manager-profile` | `tools/research/manager_profile.py:main()` |
| `main.py micro-index` | `tools/research/build_micro_index.py:main()` |
| `main.py factor-lib` | `tools/research/factor_library.py:main()` |
| `main.py leverage` | `tools/research/leverage_monitor.py:main()` |
| `main.py etf-flow` | `tools/research/etf_flow.py:main()` |

## 红线

- α 测量已冻结：`manager_profile` 只回答「像谁＋β」，不输出 α 结论。
- `leverage_monitor` / `etf_flow` 只产风险与资金面证据，不产 β 值、仓位建议、宽基配仓（D2 红线）。
- 引用沪市 ETF 份额变化必须标注 T+1（上交所口径）；深市 `fund_share` 为当日口径。
- 微盘默认**不剔 ST**（避免前视偏差）；用 `--exclude-st` 须同时声明前视偏差。
- 因子方案为**非正交 OLS**；对称正交化已弃用（会抹掉全收益化带来的 α 差异）。
- `fund_pool_builder` 九步口径顺序不可调换（2026-09-16 锁定）。
- `fund_stale` 写 `known_gaps.json` 只能经 `db stale-fix --apply`（自备份＋同步 `evidence`/`count`），且须单独批准。
