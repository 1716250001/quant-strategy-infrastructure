"""§61 返工 R1+R2+R8: index_member_all 恢复快照语义
教训: 两段式 = 职责分离，不是"多存一份"
  index_member_hist = 历史全段（含 out_date）
  index_member_all  = 当前归属快照（out_date 全空、is_new 全 Y）

§61.2 硬伤:
  1. out_date 非空 2,375 / is_new N 292 → 违反快照语义
  2. in_date > out_date 1,205 行 → 跨层拼装错位（派生时把 L1 的 in 和 L3 的 out 拼进一行）
"""
import sys, json
sys.path.insert(0, 'D:/量化策略/代码')
import pandas as pd
from pathlib import Path

HIST = Path("D:/全量数据/market_data/index_member_hist/index_member_hist.parquet")
OUT = Path("D:/全量数据/market_data/index_member_all/index_member_all.parquet")
L1 = "D:/量化策略/代码/_scratch/backups/index_member_all_before_13a.parquet"

hist = pd.read_parquet(HIST)
old = pd.read_parquet(L1)
print(f"=== 输入 ===")
print(f"  index_member_hist: {len(hist)} 行")
print(f"  index_member_all 原表（快照）: {len(old)} 行")

# === R1+R2 修复: 正确派生 ===
# 快照表: 每只股票在申万三层各取"当前归属"
# "当前归属" = out_date 空 / NaN 的那段（还在这个行业）
# in_date 统一取 L1 层（最高层级，最稳定）
# out_date 强制全空（快照语义）
# is_new 强制全 Y（快照语义）

print(f"\n=== 重建 index_member_all（快照语义）===")

def get_current(df, tier_col):
    """取每只股票当前归属（out_date 空 = 还在）"""
    # 只保留 out_date 空的行（当前在册）
    current = df[df['out_date'].isna()].copy()
    # 同一股票可能 out_date 空有多条（跨时间点），取最早 in_date 那段（最早进入这个行业）
    current = current.sort_values(['con_code', 'in_date']).groupby('con_code').first().reset_index()
    current = current.rename(columns={'index_code': tier_col})
    return current

# 拆 L1/L2/L3
l1_hist = hist[hist['src_tier'] == 'L1']
l2_hist = hist[hist['src_tier'] == 'L2']
l3_hist = hist[hist['src_tier'] == 'L3']

# 各层取当前归属（out_date 空）
l1_current = get_current(l1_hist, 'l1_code')
l2_current = get_current(l2_hist, 'l2_code')
l3_current = get_current(l3_hist, 'l3_code')

print(f"  L1 当前: {len(l1_current)} 股票 | L2: {len(l2_current)} | L3: {len(l3_current)}")

# 合并（outer join，保留所有股票）
all_new = l1_current[['con_code', 'l1_code', 'in_date']].merge(
           l2_current[['con_code', 'l2_code']], on='con_code', how='outer').merge(
           l3_current[['con_code', 'l3_code']], on='con_code', how='outer')

# 名称映射: 直接从 index_classify 读 SW2021（已在上一步拉过且已补全）
import sys, time
sys.path.insert(0, 'D:/量化策略/代码')
print(f"  名称映射从 index_classify SW2021 读（避免接口抖动）...")
ic = pd.read_parquet("D:/全量数据/market_data/index_classify/index_classify.parquet")
ic_2021 = ic[ic['src'] == 'SW2021']
l1_codes = ic_2021[ic_2021['level'] == 'L1']['index_code'].tolist()
l2_codes = ic_2021[ic_2021['level'] == 'L2']['index_code'].tolist()
l3_codes = ic_2021[ic_2021['level'] == 'L3']['index_code'].tolist()
l1_code_to_name = dict(zip(ic_2021[ic_2021['level']=='L1']['index_code'], ic_2021[ic_2021['level']=='L1']['industry_name']))
l2_code_to_name = dict(zip(ic_2021[ic_2021['level']=='L2']['index_code'], ic_2021[ic_2021['level']=='L2']['industry_name']))
l3_code_to_name = dict(zip(ic_2021[ic_2021['level']=='L3']['index_code'], ic_2021[ic_2021['level']=='L3']['industry_name']))
print(f"    L1={len(l1_codes)}/L2={len(l2_codes)}/L3={len(l3_codes)}")

all_new['l1_name'] = all_new['l1_code'].map(l1_code_to_name)
all_new['l2_name'] = all_new['l2_code'].map(l2_code_to_name)
all_new['l3_name'] = all_new['l3_code'].map(l3_code_to_name)

# 快照语义: out_date 全空 / is_new 全 Y / ts_code = con_code / name 从旧表回填
all_new['ts_code'] = all_new['con_code']
all_new['out_date'] = pd.NA  # 全空
all_new['is_new'] = 'Y'       # 全 Y
all_new['name'] = all_new['ts_code'].map(old.set_index('ts_code')['name'].to_dict())
# 补旧表没有的 name（新覆盖的 300114.SZ）
new_covering = all_new[all_new['name'].isna() | (all_new['name'] == '')]
print(f"  需补 name 的: {len(new_covering)} 只")
if len(new_covering):
    # 用 stock_basic 补
    import tushare as ts
    pro = ts.pro_api()
    for code in new_covering['ts_code'].tolist():
        df = pro.stock_basic(ts_code=code, list_status='L', fields='ts_code,name')
        if len(df):
            all_new.loc[all_new['ts_code'] == code, 'name'] = df.iloc[0]['name']

# 取 in_date: 优先 L1 的，没有 L1 再退 L2 → L3
l1_in = l1_current.set_index('con_code')['in_date'].to_dict()
l2_in = l2_current.set_index('con_code')['in_date'].to_dict()
l3_in = l3_current.set_index('con_code')['in_date'].to_dict()

def get_in_date(code):
    if code in l1_in: return l1_in[code]
    if code in l2_in: return l2_in[code]
    if code in l3_in: return l3_in[code]
    return None

all_new['in_date'] = all_new['con_code'].apply(get_in_date)

# 最终列
final_cols = ['l1_code', 'l1_name', 'l2_code', 'l2_name', 'l3_code', 'l3_name',
              'ts_code', 'name', 'in_date', 'out_date', 'is_new']
all_new = all_new[final_cols]

# 清掉 l1_code NaN（无任何归属的孤儿）
n_before = len(all_new)
all_new = all_new.dropna(subset=['l1_code'])
n_after = len(all_new)
print(f"  清理孤儿（l1_code 空）: {n_before} → {n_after}（清 {n_before-n_after}）")

print(f"\n=== 重建后 ===")
print(f"  行数: {len(all_new)}")
print(f"  out_date 非空: {all_new['out_date'].notna().sum()} / 应为 0")
print(f"  is_new 非 Y: {(all_new['is_new'] != 'Y').sum()} / 应为 0")
print(f"  in_date 空: {all_new['in_date'].isna().sum()}")
print(f"  name 空: {(all_new['name'].isna() | (all_new['name']=='')).sum()}")

# R8: 数字 groupby 实算
old_codes = set(old['ts_code'].tolist())
new_codes = set(all_new['ts_code'].tolist())
removed = old_codes - new_codes
added = new_codes - old_codes
common = old_codes & new_codes

print(f"\n=== R8 groupby 实算 ===")
print(f"  重建后行数: {len(all_new)}")
print(f"  原表行数: {len(old)}")
print(f"  移除（旧有新无）: {len(removed)} 只")
print(f"  新增（新有旧无）: {len(added)} 只 → {sorted(added)}")
print(f"  共通: {len(common)} 只")

# L1 归属变更（只看共通股票）
old_l1 = old[old['ts_code'].isin(common)].set_index('ts_code')['l1_code']
new_l1 = all_new[all_new['ts_code'].isin(common)].set_index('ts_code')['l1_code']
l1_changed_codes = (old_l1 != new_l1)
l1_changed = l1_changed_codes.sum()
print(f"  L1 归属变更: {l1_changed} 只")
# 列出变更明细
changed_codes = l1_changed_codes[l1_changed_codes].index.tolist()
print(f"    明细:")
for c in changed_codes:
    print(f"      {c}: {old_l1[c]} → {new_l1[c]}")

# R7: index_member_hist 429 vs 497
print(f"\n=== R7: 空码核查 ===")
hist_codes = set(hist['index_code'].tolist())
all_codes = set(l1_codes + l2_codes + l3_codes)
empty_codes = all_codes - hist_codes
print(f"  行业清单总码数: {len(all_codes)}")
print(f"  hist 中唯一码数: {len(hist_codes)}")
print(f"  源端空码（index_member 返回空）: {len(empty_codes)} 个")
print(f"  空码明细: {sorted(empty_codes)}")

# 分层
l2_empty = [c for c in l2_codes if c not in hist_codes]
l3_empty = [c for c in l3_codes if c not in hist_codes]
print(f"    L2 空码 {len(l2_empty)}: {l2_empty}")
print(f"    L3 空码 {len(l3_empty)}: {l3_empty}")

# 写入
if OUT.exists():
    OUT.unlink()
all_new.to_parquet(OUT, index=False)
print(f"\n✅ 已写 {OUT}")
print(f"  大小: {OUT.stat().st_size:,} 字节")
