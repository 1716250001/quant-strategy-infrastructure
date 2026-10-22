"""
§62 补正前实算：C1 / C2 / C3 真实数字 groupby 验证
审查方给的数字要我自己 groupby 复算确认
"""
import pandas as pd

hist = pd.read_parquet("D:/全量数据/market_data/index_member_hist/index_member_hist.parquet")
old = pd.read_parquet("D:/量化策略/代码/_scratch/backups/index_member_all_before_13a.parquet")
new = pd.read_parquet("D:/全量数据/market_data/index_member_all/index_member_all.parquet")

old_codes = set(old['ts_code'].tolist())
new_codes = set(new['ts_code'].tolist())
removed = sorted(old_codes - new_codes)
added = sorted(new_codes - old_codes)
common = old_codes & new_codes

# === C1: 按 .BJ 后缀实算 ===
bj = [c for c in removed if c.endswith('.BJ')]
non_bj = [c for c in removed if not c.endswith('.BJ')]
bj_920 = [c for c in removed if c.startswith('920')]
print(f"=== C1: 丢失 {len(removed)} 只按 .BJ 实算 ===")
print(f"  .BJ 后缀: {len(bj)} 只 → 审查方 354")
print(f"  其中 920xxx.BJ: {len(bj_920)} 只 → 审查方 351")
print(f"  非 .BJ: {len(non_bj)} 只 → 审查方 349")
if len(non_bj):
    print(f"  非 BJ 明细（前 20）: {non_bj[:20]}")

# 验证: 审查方说 354 BJ 中 351 是 920xxx → 那剩下 3 只是什么
bj_not_920 = [c for c in bj if not c.startswith('920')]
if bj_not_920:
    print(f"  BJ 非 920xxx: {bj_not_920}")

# === C2: L2/L3 变更 ===
old_by_code = old[old['ts_code'].isin(common)].set_index('ts_code')
new_by_code = new[new['ts_code'].isin(common)].set_index('ts_code')

for tier, col in [('L1', 'l1_code'), ('L2', 'l2_code'), ('L3', 'l3_code')]:
    old_col = old_by_code[col]
    new_col = new_by_code[col]
    changed = (old_col != new_col).sum()
    print(f"\n=== C2: {tier} 归属变更 ===")
    print(f"  变更只数: {changed} → 审查方 {'✅' if tier=='L1' else ''} L1=0 / L2=20 / L3=273")
    if changed > 0 and tier in ('L2', 'L3'):
        changed_codes = (old_col != new_col)
        codes_list = changed_codes[changed_codes].index.tolist()
        print(f"  前 10 只:")
        for c in codes_list[:10]:
            print(f"    {c}: {old_col[c]} → {new_col[c]}")

# === C3: L3 62 个空码逐码列明 ===
print(f"\n=== C3: L3 空码全列 ===")
hist_codes = set(hist['index_code'].tolist())
ic = pd.read_parquet("D:/全量数据/market_data/index_classify/index_classify.parquet")
l3_all = ic[ic['src']=='SW2021'][ic[ic['src']=='SW2021']['level']=='L3']['index_code'].tolist()
l3_empty = sorted(set(l3_all) - hist_codes)
print(f"  L3 总码: {len(l3_all)}")
print(f"  L3 空码: {len(l3_empty)} → 审查方 62")
print(f"  L3 空码明细（逐码）:")
for c in l3_empty:
    print(f"    {c}")

# L2 空码也列一下（审查方没说但完整起见）
l2_all = ic[ic['src']=='SW2021'][ic[ic['src']=='SW2021']['level']=='L2']['index_code'].tolist()
l2_empty = sorted(set(l2_all) - hist_codes)
print(f"\n  L2 空码（{len(l2_empty)}）: {l2_empty}")
l1_all = ic[ic['src']=='SW2021'][ic[ic['src']=='SW2021']['level']=='L1']['index_code'].tolist()
l1_empty = sorted(set(l1_all) - hist_codes)
print(f"  L1 空码（{len(l1_empty)}）: {l1_empty}")
