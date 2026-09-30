# -*- coding: utf-8 -*-
"""PoC-1 前置实测：主库 17 表布局与样例数据（一次性探查脚本，不入包）。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
root = Path(r"D:\全量数据\market_data")

TABLES = [
    "daily", "adj_factor", "stk_limit", "suspend_d", "namechange",
    "dividend", "moneyflow", "index_daily", "daily_basic", "fina_indicator",
    "fund_daily", "etf_limit", "index_weight", "index_dailybasic",
    "new_share", "fx_daily",
]
for table in TABLES:
    d = root / table
    files = sorted(d.glob("*.parquet")) if d.exists() else []
    sample = [f.name for f in files[:3]]
    print(f"{table:16s} exists={d.exists()} files={len(files):3d} sample={sample}")

md = root / "metadata"
if md.exists():
    print("metadata:", [f.name for f in sorted(md.glob("*"))][:10])

# 样例数据形态：daily 一年的列与 dtypes、股票列表
import pyarrow.parquet as pq

t = pq.read_table(root / "daily" / "2015.parquet")
print("\ndaily/2015.parquet:", t.num_rows, "rows")
print("  columns:", t.column_names)
print("  schema types:", {c.name: str(c.type) for c in t.schema})

# 单标的过滤实测（谓词下推验证）：读一个 ts_code
tbl = pq.read_table(
    root / "daily" / "2015.parquet",
    filters=[("ts_code", "=", "000001.SZ")],
    columns=["ts_code", "trade_date", "close", "vol", "amount"],
)
print("\n000001.SZ 2015 rows:", tbl.num_rows)
print(tbl.slice(0, 3).to_pydict())

# trade_cal 与 stock_basic
tc = pq.read_table(root / "metadata" / "trade_cal.parquet")
print("\ntrade_cal:", tc.num_rows, "rows,", tc.column_names)
sb = pq.read_table(root / "metadata" / "stock_basic.parquet")
print("stock_basic:", sb.num_rows, "rows,", sb.column_names)
