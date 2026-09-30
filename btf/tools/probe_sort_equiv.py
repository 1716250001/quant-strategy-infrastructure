# -*- coding: utf-8 -*-
"""单键(date) vs 双键(date,ts_code)排序等价性验证。

依据：若年文件 ts_code 全局升序且 Arrow sort 稳定，则稳定单键排序后
同日行保持原相对顺序（= ts_code 升序），结果与双键排序逐行相等。
等价 → iter_day_slices 可用单键排序回收 ~1.5s。
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(r"D:\全量数据\market_data")
COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"]

for table in ("daily", "fund_daily"):
    d = ROOT / table
    files = sorted(d.glob("*.parquet"))
    bad_ts, mismatch = [], []
    t_check = t_sort = 0.0
    for p in files:
        t = pq.read_table(p, columns=COLS)
        ts = t.column("ts_code")
        n = len(ts)
        t0 = time.perf_counter()
        ts_ok = n <= 1 or bool(pc.all(pc.less_equal(ts.slice(0, n - 1), ts.slice(1))).as_py())
        t_check += time.perf_counter() - t0
        if not ts_ok:
            bad_ts.append(p.name)
            continue
        t0 = time.perf_counter()
        s1 = t.sort_by([("trade_date", "ascending")])
        s2 = t.sort_by([("trade_date", "ascending"), ("ts_code", "ascending")])
        t_sort += time.perf_counter() - t0
        if not s1.equals(s2):
            mismatch.append(p.name)
    print(f"{table}: {len(files)} 文件 | ts_code 非升序 {len(bad_ts)} {bad_ts[:3]} | "
          f"单双键不等价 {len(mismatch)} {mismatch[:3]} | 升序检查 {t_check:.2f}s 双键比对 {t_sort:.2f}s")
