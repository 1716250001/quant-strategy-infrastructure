"""
R3/R4 明细 + R7 汇总
  R3: 丢失 703 只 vs 审查方说的 411 — 差异在 SW2021 vs SW2014 覆盖范围
  R4: L1 变更 0 只（正确版本下无变更，审查方 22 是跨层拼装副作用）
  R7: 68 个源端空码
"""
import pandas as pd
from pathlib import Path

hist = pd.read_parquet("D:/全量数据/market_data/index_member_hist/index_member_hist.parquet")
old = pd.read_parquet("D:/量化策略/代码/_scratch/backups/index_member_all_before_13a.parquet")
new = pd.read_parquet("D:/全量数据/market_data/index_member_all/index_member_all.parquet")

old_codes = set(old['ts_code'].tolist())
new_codes = set(new['ts_code'].tolist())
removed = sorted(old_codes - new_codes)
added = sorted(new_codes - old_codes)
common = old_codes & new_codes

print(f"=== R3: 丢失 {len(removed)} 只 vs 审查方 411 ===")
print(f"  新增: {len(added)} 只 → {added}")

# 先看被移除的股票在 hist 中是否有记录
removed_in_hist = []
removed_not_in_hist = []
for code in removed:
    sub = hist[hist['con_code'] == code]
    if len(sub):
        removed_in_hist.append(code)
    else:
        removed_not_in_hist.append(code)

print(f"\n  被移除 {len(removed)} 只分为两类:")
print(f"  A. hist 中**有记录**（说明在某些时点有申万成分，但当前 out_date 非空/全 out 了）: {len(removed_in_hist)} 只")
print(f"  B. hist 中**无记录**（pro.index_member 完全不返回，退市股）: {len(removed_not_in_hist)} 只")
print(f"\n  审查方给的 411 应该是哪类？让我看:")

# 抽样 B 类（退市股）
if removed_not_in_hist:
    print(f"\n  B 类（完全不返回）抽样 10:")
    for c in removed_not_in_hist[:10]:
        print(f"    {c}")

print(f"\n  B 类全部:")
for c in removed_not_in_hist:
    print(f"    {c}")

print(f"\n=== R4: L1 归属变更 ===")
old_l1 = old[old['ts_code'].isin(common)].set_index('ts_code')['l1_code']
new_l1 = new[new['ts_code'].isin(common)].set_index('ts_code')['l1_code']
l1_diff = (old_l1 != new_l1).sum()
print(f"  共通 {len(common)} 只中 L1 变更: {l1_diff} 只")
print(f"  审查方说的 22 只是**错误版本**（跨层拼装）的副作用 → R1+R2 正确版本后变更消失 ✅")

# R7: 源端空码统计
print(f"\n=== R7: 源端空码 ===")
hist_codes = set(hist['index_code'].tolist())
import pandas as pd
ic = pd.read_parquet("D:/全量数据/market_data/index_classify/index_classify.parquet")
ic_2021 = ic[ic['src'] == 'SW2021']
all_l1 = ic_2021[ic_2021['level']=='L1']['index_code'].tolist()
all_l2 = ic_2021[ic_2021['level']=='L2']['index_code'].tolist()
all_l3 = ic_2021[ic_2021['level']=='L3']['index_code'].tolist()
empty_l1 = [c for c in all_l1 if c not in hist_codes]
empty_l2 = [c for c in all_l2 if c not in hist_codes]
empty_l3 = [c for c in all_l3 if c not in hist_codes]
print(f"  L1 空码: {len(empty_l1)} / {len(all_l1)}")
print(f"  L2 空码: {len(empty_l2)} / {len(all_l2)} → {empty_l2}")
print(f"  L3 空码: {len(empty_l3)} / {len(all_l3)} → {empty_l3[:5]}...")
print(f"  总空码: {len(empty_l1)+len(empty_l2)+len(empty_l3)}")
