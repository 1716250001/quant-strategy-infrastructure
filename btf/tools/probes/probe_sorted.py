# -*- coding: utf-8 -*-
"""年文件日期有序性检测（B1 优化机会验证：有序 → iter_day_slices 可跳过 sort_by）。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(r"D:\全量数据\market_data")

for table in ("daily", "fund_daily"):
    d = ROOT / table
    if not d.is_dir():
        print(f"{table}: MISSING")
        continue
    unsorted_files = []
    for p in sorted(d.glob("*.parquet")):
        t = pq.read_table(p, columns=["trade_date"])
        col = t.column("trade_date")
        n = len(col)
        ok = n <= 1 or bool(pc.all(pc.less_equal(col.slice(0, n - 1), col.slice(1))).as_py())
        if not ok:
            unsorted_files.append(p.name)
    print(f"{table}: {len(list(d.glob('*.parquet')))} 个年文件，非有序 {len(unsorted_files)} 个"
          + (f" -> {unsorted_files[:5]}" if unsorted_files else "（全部日期有序 ✓）"))
