# -*- coding: utf-8 -*-
"""B1 9.74s 成本分解：I/O / 有序性检查 / 双键排序 / 单键排序 / pylist / 分组 各占多少。

目的：确定余量回收路径（跳过排序不可行——年文件非日期有序，探明真实排序键）。
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(r"D:\全量数据\market_data")
COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"]
YEARS = range(2015, 2025)

# 1) 纯 I/O + 列裁剪
tables = []
t0 = time.perf_counter()
for y in YEARS:
    tables.append(pq.read_table(ROOT / "daily" / f"{y}.parquet", columns=COLS))
t_read = time.perf_counter() - t0
n_rows = sum(t.num_rows for t in tables)
print(f"1) I/O+列裁剪 10年({n_rows:,}行): {t_read:.2f}s")

# 2) 真实排序键探明：(ts_code, trade_date) 联合有序？
t0 = time.perf_counter()
all_ts_sorted = True
all_joint = True
for y, t in zip(YEARS, tables):
    ts = t.column("ts_code")
    n = len(ts)
    ok_ts = n <= 1 or bool(pc.all(pc.less_equal(ts.slice(0, n - 1), ts.slice(1))).as_py())
    joint = t.equals(t.sort_by([("ts_code", "ascending"), ("trade_date", "ascending")]))
    all_ts_sorted &= ok_ts
    all_joint &= joint
    if not joint:
        print(f"   {y}: ts_code升序={ok_ts}, (ts_code,date)联合有序={joint}")
t_check = time.perf_counter() - t0
print(f"2) 有序性检查: ts_code 全升序={all_ts_sorted}, (ts_code,date) 联合有序={all_joint}（{t_check:.2f}s）")

# 3) 双键排序（现行实现）
t0 = time.perf_counter()
s2 = [t.sort_by([("trade_date", "ascending"), ("ts_code", "ascending")]) for t in tables]
t_sort2 = time.perf_counter() - t0
print(f"3) 双键 sort_by(date,ts_code) 10年: {t_sort2:.2f}s")

# 4) 单键排序（date only，若 ts_code 块内 date 有序则稳定排序结果等价）
t0 = time.perf_counter()
s1 = [t.sort_by([("trade_date", "ascending")]) for t in tables]
t_sort1 = time.perf_counter() - t0
print(f"4) 单键 sort_by(date) 10年: {t_sort1:.2f}s")
if all_joint:
    eq = all(a.equals(b) for a, b in zip(s1, s2))
    print(f"   单键 vs 双键结果一致（ts_code 稳定性成立）: {eq}")

# 5) 全列 to_pylist（现行实现）
t0 = time.perf_counter()
pyl = []
for t in s2:
    pyl.append({n: t.column(n).to_pylist() for n in t.column_names})
t_pylist = time.perf_counter() - t0
print(f"5) 全列 to_pylist({len(COLS)}列×{n_rows:,}值) 10年: {t_pylist:.2f}s")

# 6) 分组：Python while 循环（现行实现）
t0 = time.perf_counter()
n_days = 0
for cols in pyl:
    dates = cols["trade_date"]
    i, n = 0, len(dates)
    while i < n:
        j = i
        day = dates[i]
        while j < n and dates[j] == day:
            j += 1
        n_days += 1
        i = j
t_group = time.perf_counter() - t0
print(f"6) Python while 分组({n_days} 日): {t_group:.2f}s")

# 7) 分组：向量化 run 边界（替代方案）
t0 = time.perf_counter()
n_days_v = 0
for t in s2:
    d = t.column("trade_date")
    n = d.length()
    if n == 0:
        continue
    changed = pc.not_equal(d.slice(0, n - 1), d.slice(1))
    idx = np.flatnonzero(changed.to_numpy(zero_copy_only=False))
    starts = np.concatenate([[0], idx + 1])
    n_days_v += len(starts)
t_vgroup = time.perf_counter() - t0
print(f"7) 向量化 run 边界分组({n_days_v} 日): {t_vgroup:.2f}s")

total = t_read + t_sort2 + t_pylist + t_group
print(f"\n合计（现行路径 1+3+5+6）: {total:.2f}s   （B1 实测 9.74s，差额为 feed/生成器开销）")
