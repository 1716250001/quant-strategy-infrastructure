#-*- coding: utf-8 -*-
"""
tools/fund_pool_builder.py — 公募主动权益基金池构建（清洗口径锁定版）

口径（顺序不可调换，2026-09-16 锁定）
----
0. 全市场基金                             32,777
1. 权益类 fund_type ∈ {股票型, 混合型}       21,621
2. 三重兜底剔被动(类型|名字|基准单成分)        14,154
3. 剔分级基金(前缀15/16/167/150 + 含"分级")    14,144
4. 剔 FOF/QDII/跨境                        12,491
5. 净值∪行情 双载体并集                     12,237
6. 规模 ≥ 2亿（六因子R²拐点实证）             5,684
7. 波动率 ≥ 8%（跳过前90天建仓期）             5,110
8. 份额归并（管理人+名称主干，不用成立日）        2,902
9. 净值 ≥ 250日                            2,336
                                          ────────
最终可挖池                                  2,336   压缩14:1

设计要点
--------
- 顺序即设计：先规模后债性与反之结果不同
- 空壳文件（≤5行）须过滤，否则污染分子分母
- adj_nav 存在 2026-06-02 口径断层，须检测
- 消歧降级为可选增强：分析单位=「基金×任职区间」，重名仅2.9%

用法
----
    python -m tools.fund_pool_builder                # 输出到 .temp/pool.csv
    python -m tools.fund_pool_builder --out pool.csv
"""
import os, re, glob, argparse, sys

import pandas as pd
import numpy as np

# 支持 python -m tools.fund_pool_builder 直接运行（与 python main.py 等价）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import reader

DEFAULT_DATA = r"D:\全量数据\market_data"


def parse_bench(b):
    """返回 (成分数, 最大单一成分权重占比)"""
    b = str(b)
    if not b or b.lower() == "nan":
        return (np.nan, np.nan)
    segs = [s for s in re.split(r"[+＋]", b) if s.strip()]
    ws = []
    for s in segs:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if m:
            w = float(m.group(1))
            if w <= 100:
                ws.append(w)
    if not ws:
        return (np.nan, np.nan)
    return (len(ws), max(ws) / sum(ws))


def ann_vol(d, skip_days=90, min_rows=100):
    """年化波动率（跳过建仓期前90天，剔除异常）

    参数 d: 该基金的净值 DataFrame（已含 nav_date/adj_nav/unit_nav 列）。
            ⚠ 改为接收 DataFrame 而非文件路径：fund_nav 已迁为按年分区，
              逐只按 {code}.parquet 读会全部失败（且不报错，只是静默返回 nan）。
    """
    try:
        d = d.dropna(subset=["nav_date"])
        col = "adj_nav" if d["adj_nav"].notna().sum() > 20 else "unit_nav"
        d = d.copy()
        d["dt"] = pd.to_datetime(d["nav_date"], format="%Y%m%d", errors="coerce")
        s = d.dropna(subset=[col, "dt"]).sort_values("dt")
        s = s[s[col] > 0]
        if len(s) < 120:
            return np.nan
        s = s[s["dt"] >= s["dt"].min() + pd.Timedelta(days=skip_days)]
        r = s.set_index("dt")[col].pct_change().dropna()
        r = r[r.abs() < 0.25]
        return float(r.std() * np.sqrt(245)) if len(r) >= min_rows else np.nan
    except Exception:
        return np.nan


def detect_adj_break(d, cut="20260602"):
    """检测 adj_nav 口径断层（比值归属切换）—— 参数同上，接收 DataFrame。"""
    try:
        d = d.dropna(subset=["nav_date"]).copy()
        d["nav_date"] = d["nav_date"].astype(str)
        d = d[(d["unit_nav"] > 0) & (d["adj_nav"] > 0)].sort_values("nav_date")
        bef, aft = d[d["nav_date"] < cut].tail(15), d[d["nav_date"] >= cut].head(15)
        if len(bef) < 5 or len(aft) < 5:
            return False

        def who(x):
            r_u = (x["adj_nav"] / x["unit_nav"] - 1).abs().median()
            r_n = (x["adj_nav"] / x["accum_nav"] - 1).abs().median() if x["accum_nav"].notna().sum() >= 5 else np.nan
            if np.isnan(r_n):
                return "?"
            return "unit" if r_u < r_n else "accum"
        a, b = who(bef), who(aft)
        return (a != b) and ("?" not in (a, b))
    except Exception:
        return False


def build(data_dir=DEFAULT_DATA, out=None):
    META = os.path.join(data_dir, "metadata")
    NAV = os.path.join(data_dir, "fund_nav")
    FD = os.path.join(data_dir, "fund_daily")

    fb = pd.read_parquet(os.path.join(META, "fund_basic.parquet")).fillna("")
    # fund_nav / fund_daily 已改为按年分区，旧写法（glob *.parquet 取文件名）
    # 在 by_year 下会把年份当代码，故改走 reader（可指定库根）。
    nav_set = set(reader.codes_from_store("fund_nav", root=data_dir))
    fd_set = set(reader.codes_from_store("fund_daily", root=data_dir))
    fb["has_any"] = fb["ts_code"].isin(nav_set) | fb["ts_code"].isin(fd_set)
    fb[["n_comp", "max_w"]] = fb["benchmark"].apply(lambda x: pd.Series(parse_bench(x)))

    log = []

    def step(name, df):
        log.append((name, len(df)))
        return df

    S = fb.copy()
    S = step("0. 全市场", S)
    S = step("1. 权益类(股票+混合)", S[S["fund_type"].isin(["股票型", "混合型"])])

    m_type = S["invest_type"].isin(["被动指数型", "增强指数型"])
    m_name = S["name"].str.contains("联接|ETF|指数|LOF|MSCI|中证|国证|沪深|恒生|标普|纳指", na=False)
    m_bench = (S["n_comp"] == 1) & (S["max_w"] >= 0.99)
    S = step("2. 三重兜底剔被动", S[~(m_type | m_name | m_bench)])

    m_fj = S["ts_code"].str.match(r"^(15|16|167|150)\d") & S["name"].str.contains("分级", na=False)
    S = step("3. 剔分级基金", S[~m_fj])
    S = step("4. 剔FOF/QDII", S[~S["name"].str.contains("FOF|QDII|美元|纳斯达克|标普|全球|海外", na=False)])
    S = step("5. 有载体", S[S["has_any"]])

    # ── 预读 fund_nav 面板，按标的建内存索引 ──
    # 为什么预读: fund_nav 已迁为按年分区，逐只按 {code}.parquet 读会全部
    # 失败（返回空 → 规模/波动/行数/断层全为空，且不报错）。
    # 一次性读年度文件仅需 0.7 秒，比逐一打 2 万个小文件还快。
    print("  预读 fund_nav 面板...")
    panel = reader.read_range(
        "fund_nav",
        columns=["ts_code", "nav_date", "net_asset", "adj_nav",
                 "unit_nav", "accum_nav"],
        root=data_dir,
    )
    if panel.empty:
        print("  [ERROR] fund_nav 读取为空，无法计算规模/波动")
        return S
    nav_by_code = {c: g for c, g in panel.groupby("ts_code", sort=False)}
    del panel
    print(f"  面板就绪: {len(nav_by_code)} 只基金")

    sz = {}
    for c in S["ts_code"]:
        d = nav_by_code.get(c)
        if d is None:
            continue
        try:
            dn = d.dropna(subset=["net_asset"])
            if not dn.empty:
                sz[c] = float(dn.sort_values("nav_date").iloc[-1]["net_asset"]) / 1e8
        except Exception:
            continue
    S = S.copy()
    S["size_yi"] = S["ts_code"].map(sz)
    S = step("6. 规模>=2亿", S[(S["size_yi"].isna()) | (S["size_yi"] >= 2)])

    S = S.copy()
    S["vol"] = S["ts_code"].map(
        lambda c: ann_vol(nav_by_code[c]) if c in nav_by_code else np.nan)
    S = step("7. 剔债性(<8%)", S[(S["vol"].isna()) | (S["vol"] >= 0.08)])

    SUF = re.compile(r"[-–—]?([A-Z]{1,2})$")
    S = S.copy()
    S["key"] = S["management"] + "|" + S["name"].apply(
        lambda n: SUF.sub("", re.sub(r"\(.*?\)", "", str(n))).strip())
    S = step("8. 份额归并", S.drop_duplicates("key"))

    def nrow(c):
        d = nav_by_code.get(c)
        return 0 if d is None else len(d)
    S = S.copy()
    S["n_rows"] = S["ts_code"].map(nrow)
    S = step("9. 净值>=250日", S[S["n_rows"] >= 250])

    # 断层标记
    S = S.copy()
    S["adj_break"] = S["ts_code"].map(
        lambda c: detect_adj_break(nav_by_code[c]) if c in nav_by_code else False)

    print("清洗漏斗（顺序不可调换）:")
    prev = None
    for name, n in log:
        print(f"  {name:<24}: {n:>6}{'' if prev is None else f' ({n - prev:+,})'}")
        prev = n
    print(f"  {'=' * 44}")
    print(f"  最终池: {log[-1][1]} 只")

    if not out:
        # 默认落到 config.TEMP_DIR（workspace/.temp），并确保目录存在 ——
        # 此前默认路径经 ".." 推算指向不存在的目录，跑到最后一步才报
        # "Cannot save file into a non-existent directory"。
        from config import TEMP_DIR
        out = os.path.join(TEMP_DIR, "pool.csv")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    S.to_csv(out, index=False, encoding="gbk")
    print(f"已保存 → {out}")
    return S


def main(argv=None):
    """CLI 入口。

    argv: 参数列表（None 则取 sys.argv[1:]），便于从统一入口 main.py 调用。
    """
    ap = argparse.ArgumentParser(
        description="公募主动权益基金池构建（清洗口径锁定版）")
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    return build(a.data_dir, a.out)


if __name__ == "__main__":
    main()
