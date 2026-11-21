# tools/export — 场内基金清单与导出子包（ETF/LOF 分类 · Excel 交付）

> 事实锚点：2026-11-21 ｜ 模块数：3 ｜ 调用方式：`python main.py lof-list` / `lof-export`，或 `python -m tools.export.<模块>`

## 职责

- 从主库 `fund_basic` × `fund_daily` 判定「在场内交易」的基金，按代码前缀分 ETF / LOF / 场外 / 其他。
- `list_lof` 打印分类统计与 LOF 明细；`export_lof` 产出含投资方向细分的 Excel，落 `common.paths.DOC_DIR`。
- 只读库、只产出 Excel；默认落 `common.paths.DOC_DIR`，可用 `--out` 覆盖；不取数、不改库、不参与决策计算。

## 模块清单

| 文件 | 作用 | 对外入口 |
|---|---|---|
| `list_lof.py` | 场内基金分类统计＋LOF 明细打印 | `main.py lof-list` → `run()` |
| `export_lof.py` | 场内 LOF 全表＋投资方向（含 REITs/QDII/FOF 细分）导出 Excel | `main.py lof-export` → `run(out_path=None)` |
| `fund_classify.py` | `classify_fund(row, include_53)` 分类纯函数，两脚本共用 | 仅 import |

## 与 CLI 的对应关系

| CLI | 实现 |
|---|---|
| `main.py lof-list` | `tools/export/list_lof.py:run()`（`cli/misc_cmds.cmd_lof_list`） |
| `main.py lof-export` | `tools/export/export_lof.py:run()`（`cli/misc_cmds.cmd_lof_export`） |
| —（无独立命令） | `tools/export/fund_classify.py:classify_fund()` |

## 红线

- 只读 `fund_basic` / `fund_daily`，不得写入库或断点。
- 布局无关：在场代码集必须走 `reader.codes_from_store("fund_daily")`；不得从文件名取代码（by_year 下会把年份当代码）。
- 输出默认取 `common.paths.DOC_DIR`（`--out` 可覆盖），不得硬编码绝对路径。
- `include_53` 口径固定：`list_lof` 传 `False`，`export_lof` 用默认 `True`；改一处须同步另一处。
- 分类只按 `.SH/.SZ/.OF` 后缀与代码前缀，不引入业务/名称主观判断。
- `export_lof.invest_desc` 的 REITs 细分按名称关键词匹配，新增品类须同时补分支。
