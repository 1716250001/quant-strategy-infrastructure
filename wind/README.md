# wind — Wind 第三库（目录索引 + 唯一调用闸门）

> 事实锚点：2026-11-21 ｜ 模块数：3 个生产模块（另有 `__init__`）｜ CLI 入口：`python -m wind.catalog --build` / `python -m wind.wind_gate`

## 职责

- 管理 `D:\全量数据\wind_data`：`raw` 原始响应、`norm/series` 序列、`norm/snap` 快照、`_catalog` 目录索引、`_meta` 预算台账。
- `catalog` 扫描 4,142 个 `wind_index_daily` parquet 与 3,440 个 raw JSON，生成 `wind_catalog.parquet`（4,157 行）并过门槛校验。
- `wind_gate` 作为所有 Wind 调用唯一入口：缓存命中、限流、先存后解析、预算记账、快照禁覆盖、三项自验。
- 不管理主库/alt 库数据；不进入日更组或任何 automation；`call()` 当前为 mock 骨架，未接入真实 Wind 客户端。

## 关键模块

| 文件 | 作用 |
|---|---|
| `__init__.py` | 包说明：WIND 第三库工具包（§69/§70） |
| `config.py` | 库根/路径、写保护区 `assert_writable`、越界计数、凭据、限流与预算软闸、ISO8601 日期归一化 |
| `catalog.py` | 探测 raw/series → 并集建 `wind_catalog.parquet` → §70.4 门槛校验（`--probe-only` / `--build` / `--verify-only`） |
| `wind_gate.py` | 唯一调用入口 `call()`：qid 归一化、缓存、限流、先存后解析、记账、`write_snap` 禁覆盖、`selfcheck()` |

## 与其他包的关系

- 单向依赖 `config`（路径）与 `common.parquet_store`（原子写）；`config` 不反向依赖 wind。
- `catalog` 只读 `wind_data/raw` 与 `norm/series`；`wind_gate` 写 `raw/`、`norm/snap/` 与 `_meta/budget_ledger.jsonl`。
- `main.py` 无 wind 子命令；下游本地微盘指数由 `micro-index` 自建替代 Wind `8841431.WI`。
- 禁止反向依赖：`fetch`/`alt` 不 import wind，wind 不写主库与 alt 库。

## 红线

- `wind_gate.call` 是唯一调用入口，无旁路；调用前必须过 `_cooldown`（`WIND_CALL_INTERVAL=0.6s`）与预算软闸。
- 先存后解析：响应先落 `raw/{yyyymmdd}/{qid}__{hash8}.json` 再解析；raw 只增不改。
- 快照不可覆盖：`write_snap` 目标存在且 `force=False` 抛 `WindGateError`，写盘走原子写。
- 写保护比 alt 更严：`FORBIDDEN_WRITE_ROOTS` 同时含主库、alt 库、项目备份区；越界抛 `WindPathViolationError` 并计数。
- 凭据只从环境变量 `WIND_API_KEY` 读取；`WIND_RPC_TIMEOUT=60`（注释明示禁设 200）。
