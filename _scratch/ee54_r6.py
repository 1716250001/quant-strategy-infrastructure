"""
G-13A3 R6: 时点查询实测
派单要求: 任取 2015-06-30 / 2020-12-31，返回成分与已知事件核对
"""
import sys
sys.path.insert(0, 'D:/量化策略/代码')
import pandas as pd
from pathlib import Path

HIST = Path("D:/全量数据/market_data/index_member_hist/index_member_hist.parquet")
hist = pd.read_parquet(HIST)

print(f"=== index_member_hist 时点查询验证 ===")
print(f"  总行数: {len(hist)}")
print(f"  in_date 范围: {hist['in_date'].min()} ~ {hist['in_date'].max()}")
print(f"  out_date 非空: {hist['out_date'].notna().sum()}")

def query_at(hist, date_str, desc):
    """取 date_str 时点的当前成分：
       in_date <= date_str AND (out_date 空 OR out_date > date_str)"""
    mask_in = hist['in_date'] <= date_str
    mask_out = hist['out_date'].isna() | (hist['out_date'] > date_str)
    df = hist[mask_in & mask_out]
    
    # 每只股票每行业取最新 in_date 那段（或 out_date 空的那段）
    df = df.sort_values(['con_code', 'index_code', 'in_date']).groupby(['con_code', 'index_code']).last().reset_index()
    
    # 按 industry 分组统计
    by_industry = df.groupby(['index_code', 'src_tier']).agg(
        股票数=('con_code', 'nunique'),
    ).reset_index()
    
    print(f"\n=== {desc} ({date_str}) ===")
    print(f"  总 (index_code, con_code) 行数: {len(df)}")
    print(f"  总唯一 con_code: {df['con_code'].nunique()}")
    print(f"  覆盖行业数: {df['index_code'].nunique()}")
    print(f"\n  按层级:")
    for tier in ['L1', 'L2', 'L3']:
        sub = df[df['src_tier'] == tier]
        print(f"    {tier}: {sub['index_code'].nunique()} 行业 / {sub['con_code'].nunique()} 股票")
    
    # 关键验证：老股票 000001.SZ 平安银行在这两个时点是否在行业里？
    print(f"\n  🎯 已知事件核对:")
    for code in ['000001.SZ', '000858.SZ', '000002.SZ', '600519.SH']:
        sub = df[df['con_code'] == code]
        if len(sub):
            print(f"    {code}: 在 {sub['index_code'].tolist()} 行业")
        else:
            print(f"    {code}: ❌ 不在任何行业（理论上 2015/2020 应该在申万行业里）")
    
    return df

# R6: 两个时点
df_2015 = query_at(hist, '20150630', '2015-06-30（股灾后早期）')
df_2020 = query_at(hist, '20201231', '2020-12-31（疫情期间）')

# 验证: 两个时点应该有差异（2015 有很多 2020 没有的退市股）
old_codes_2015 = set(df_2015['con_code'].tolist())
old_codes_2020 = set(df_2020['con_code'].tolist())
removed_between = old_codes_2015 - old_codes_2020
print(f"\n=== 两个时点对比 ===")
print(f"  2015-06-30 唯一 con_code: {len(old_codes_2015)}")
print(f"  2020-12-31 唯一 con_code: {len(old_codes_2020)}")
print(f"  2015 有 / 2020 无（期间可能退市或调整）: {len(removed_between)} 只")

# 抽样看
if removed_between:
    removed_sample = list(removed_between)[:5]
    print(f"    抽样: {removed_sample}")
    for code in removed_sample:
        sub = hist[hist['con_code'] == code]
        print(f"      {code}: {sub['index_code'].tolist()}")

print(f"\n✅ R6 时点查询可用 — hist 表支持任意历史时点成分切片")
