import pandas as pd

hist = pd.read_parquet("D:/全量数据/market_data/index_member_hist/index_member_hist.parquet")
old = pd.read_parquet("D:/量化策略/代码/_scratch/backups/index_member_all_before_13a.parquet")
new = pd.read_parquet("D:/全量数据/market_data/index_member_all/index_member_all.parquet")
ic = pd.read_parquet("D:/全量数据/market_data/index_classify/index_classify.parquet")

old_codes = set(old['ts_code'].tolist())
new_codes = set(new['ts_code'].tolist())
removed = sorted(old_codes - new_codes)
common = old_codes & new_codes

# C1: BJ 实算
bj = [c for c in removed if c.endswith('.BJ')]
non_bj = [c for c in removed if not c.endswith('.BJ')]
print(f"C1_BJ_TOTAL={len(bj)}")
print(f"C1_920xxx={len([c for c in bj if c.startswith('920')])}")
print(f"C1_BJ_OTHER={[c for c in bj if not c.startswith('920')]}")
print(f"C1_NON_BJ={len(non_bj)}")

# C2: L1/L2/L3 变更
old_c = old[old['ts_code'].isin(common)].set_index('ts_code')
new_c = new[new['ts_code'].isin(common)].set_index('ts_code')
for tier, col in [('L1', 'l1_code'), ('L2', 'l2_code'), ('L3', 'l3_code')]:
    chg = (old_c[col] != new_c[col]).sum()
    print(f"C2_{tier}_CHANGED={chg}")

# C3: L3 空码全列
hc = set(hist['index_code'].tolist())
l3_all = ic[(ic['src']=='SW2021') & (ic['level']=='L3')]['index_code'].tolist()
l3_empty = sorted(set(l3_all) - hc)
print(f"C3_L3_EMPTY_COUNT={len(l3_empty)}")
for c in l3_empty:
    print(f"L3EMPTY {c}")
l2_all = ic[(ic['src']=='SW2021') & (ic['level']=='L2')]['index_code'].tolist()
l2_empty = sorted(set(l2_all) - hc)
print(f"C3_L2_EMPTY={l2_empty}")
print(f"C3_L2_EMPTY_COUNT={len(l2_empty)}")

# 额外：L2/L3 变更原因（全是 nan？）
l2_chg = (old_c['l2_code'] != new_c['l2_code'])
l2_nan = l2_chg & (new_c['l2_code'].isna())
print(f"C2_L2_CHANGED_TO_NAN={l2_nan.sum()}")
l3_chg = (old_c['l3_code'] != new_c['l3_code'])
l3_nan = l3_chg & (new_c['l3_code'].isna())
print(f"C2_L3_CHANGED_TO_NAN={l3_nan.sum()}")
