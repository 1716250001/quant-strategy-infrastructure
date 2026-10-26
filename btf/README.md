# btf — A 股日线回测工具

> 版本 **6.0.0**（单一真源 `btf/_version.py`）｜Python 3.13｜单人本机使用
> 一份配置 + 一个策略类 → 按日循环跑完整段回测 → 产出可复核的指标与产物。

---

## 1. 它是什么，不是什么

**是**：事件驱动日线回测内核。记账、撮合、成本、风控、绩效、产物落盘，一条链走完。

**不是**：
- **不含策略实现**。`btf/strategy/` 只有 5 个接口文件（`base` / `context` / `rebalance` / `rules` / `__init__`），策略在**仓外**，用 `run.strategy_paths` 指到父目录装载。
- **不含数据**。数据从 `data.feed` 指向的按年分区 Parquet 主库读取（默认经 `BTF_DATA_DIR` 覆盖）。
- **不含规则参数**。规则值只从 `rules_mirror.v1` 镜像 JSON 读；YAML 里出现规则参数字面量会被 schema 拒绝。

> 这三条是刻意的边界：工具可独立升级、策略可替换、口径不会被配置手抄污染。

---

## 2. 30 秒跑通

```bash
# 1) 环境（本仓已带 venv 时直接用它）
python -m pip install -e .

# 2) 版本与门禁
bt --version          # btf 6.0.0
bt check              # 十项质量门，须 10/10

# 3) 跑一个自带示例
bt run -c examples/config_rotation.yaml

# 4) 复核产物（digest 一致才算可复现）
bt verify <run_id>
```

产物落在 `BTF_OUTPUT_DIR`（默认工作区 `回测产物/runs/<run_id>/`）：
`manifest.json` / `metrics.json` / `snapshots.jsonl` / `fills.jsonl` / `trades.jsonl` / `rejections.jsonl` / `events.jsonl` / `data_quality_report.json`

---

## 3. 目录结构

```
btf/
├─ btf/              核心包（17 子包：domain/data/strategy/engine/execution/risk/
│                    portfolio/analytics/experiment/optimize/runtime/app/viz/
│                    registry/config/cli + btf_datasets）
├─ schemas/          配置 JSON Schema（backtest.v1.json，唯一一份）
├─ examples/         示例策略 rotation.py + 示例配置（不进 wheel）
├─ tools/            常驻工具 28 个（门禁脚本 / 对账 / 复跑 / 冷备 / 审计）
├─ tests/            1242 项（l1–l5 + smoke 五层）
├─ benchmarks/       B1–B5 基准留档
├─ CHANGELOG.md      版本留痕（Keep a Changelog）
└─ pyproject.toml    ruff + import-linter 十契约 + pytest 标记
```

**进了 wheel 的只有 `btf*` 与 `examples*`**（`pyproject` 的 `packages.find.include`）。`tools/` 与 `tests/` 是仓内工具，不进发布包。

---

## 4. 策略放在哪（最容易踩的一条）

策略**不在本仓**。用 `run.strategy` 给点路径，`run.strategy_paths` 给**父目录**：

```yaml
run:
  strategy: "my_strategies.rotation:MomentumRotation"   # module:Class
  strategy_paths: ["D:/path/to/parent_of_my_strategies"]
```

装载前会做**同名包冲突检测**（异源同名 ⇒ `ConfigError`）。

`tests/fixtures/strategy_contract.py` 是策略契约的最小样例，照着它写即可。

---

## 5. 数据要求

- 布局：**按年分区 Parquet**（`daily/2015.parquet` 这类），表名与列名须符合 `05-数据架构与数据集体系.md`。
- 路径：默认由包根锚点派生，可用 `BTF_DATA_DIR` 覆盖；**不要**在配置里写死盘符。
- Feed 选择：
  - `tushare_parquet` —— **只读 `daily`**（股票宇宙）
  - `mixed` —— 按代码段自动路由三表（`daily` / `fund_daily` / `index_daily`）
  - 宇宙里有 ETF/LOF/指数却用了 `tushare_parquet` ⇒ **装配期直接 `ConfigError`**（不静默跑出 0 交易日）

---

## 6. 常用命令

| 命令 | 用途 |
|---|---|
| `bt check` | 十项质量门（改代码前后都跑） |
| `bt run -c <yaml>` | 跑回测 |
| `bt runs --latest 5` | 列最近几个 run 的摘要（只读） |
| `bt show --run <id>` | 单 run 的 manifest + 指标摘要（只读） |
| `bt verify <run_id>` | 复核 metric digest 与产物完整性 |
| `bt report <run_id>` | 生成 HTML/Markdown 报告（幂等） |
| `bt test` | 按层跑测试（`--layer`） |
| `bt dataset freeze --config <yaml>` | 冻结数据集指纹（`content_hash` 与门禁同源） |
| `bt optimize` | 网格搜索 |
| `bt validate` | walk-forward / 蒙特卡洛 |
| `bt adhoc` | **一次性/外部回测脚本**的产物契约封装（跑仓外策略时用它最省事） |
| `bt config-check <yaml>` | 仅校验配置（不跑） |

工具类（`tools/`）：`schema_guard.py` 配置键一致性 · `feed_universe_check.py` feed↔标的路由预检 · `zero_fill_check.py` 零成交普查 · `stkdiv_audit.py` 送转审计 · `delist_audit.py` 退市残仓审计 · `cold_backup.py` 冷备。

---

## 7. 出问题先看哪

| 症状 | 先查 |
|---|---|
| 配置被拒 | `bt config-check <yaml>` 的具体键名 |
| `0 个交易日` / `n_fills=0` | feed 是否选错（见 §5）；产物里 `data_version.tables` 是否含预期表 |
| 指标是 `null` | 零成交或零交易日时，基准相对项**按设计不输出**（不是缺 bug） |
| 数字与旧 run 对不上 | 先核 `manifest.data_version.content_hash` 与 `rules_version`，再核 `run.price_view` |
| 门禁红 | `bt check` 会逐项给原因；日志在 `_scratch/bt_check_logs/` |

---

## 8. 三条纪律（不是建议，是硬约束）

1. **可复现性优先于便利**。同一份配置跨版本跑出不同数字，是不可接受的；宁可报错也不静默纠正。
2. **验证话术**：不以「全绿」作结论，必须附实测输出摘要。本仓的自检报告格式见 `自检报告-v5.0.0.md`。
3. **改动必须过门禁并在 `CHANGELOG.md` 追加条目**；架构性变更另需登记 `18-分步编码计划.md`。
