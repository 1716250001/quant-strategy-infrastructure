# -*- coding: utf-8 -*-
"""B1 超标剖析（CP1 预案第一步：定位瓶颈在 I/O 还是 Python 对象化）。"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import pyarrow.parquet as pq

ROOT = Path(r"D:\全量数据\market_data")
COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"]

t0 = time.perf_counter()
# 1) 纯 I/O + 列裁剪（10 年）
parts = []
for y in range(2015, 2025):
    parts.append(pq.read_table(ROOT / "daily" / f"{y}.parquet", columns=COLS))
t_io = time.perf_counter() - t0
n = sum(p.num_rows for p in parts)
print(f"1) Arrow I/O+列裁剪 10年({n:,}行): {t_io:.2f}s")

# 2) to_pylist 代价（单列）
t0 = time.perf_counter()
dates = parts[0].column("trade_date").to_pylist()
t_list1 = time.perf_counter() - t0
print(f"2) 单列 to_pylist(1年 {len(dates):,}行): {t_list1*1000:.0f}ms → 10年9列估 {t_list1*90:.1f}s")

# 3) dataclass 构造代价
from btf.domain.market import Bar
from btf.domain.types import TradingDate

td = TradingDate.from_ymd("20240102")
t0 = time.perf_counter()
codes = parts[0].column("ts_code").to_pylist()
opens = parts[0].column("open").to_pylist()
for i in range(100_000):
    Bar(symbol=codes[i], date=td, open=opens[i], high=opens[i], low=opens[i],
        close=opens[i], pre_close=opens[i], volume=1.0, amount=1.0)
t_bar = (time.perf_counter() - t0) / 100_000
print(f"3) Bar 构造: {t_bar*1e6:.2f}μs/个 → 950万个 ≈ {t_bar*9.5e6:.1f}s")

# 4) 排序代价（全局 sort_by）
import pyarrow as pa

t0 = time.perf_counter()
big = pa.concat_tables(parts)
t_concat = time.perf_counter() - t0
t0 = time.perf_counter()
big2 = big.sort_by([("trade_date", "ascending"), ("ts_code", "ascending")])
t_sort = time.perf_counter() - t0
print(f"4) concat {t_concat:.1f}s + 全局 sort_by {t_sort:.1f}s")
