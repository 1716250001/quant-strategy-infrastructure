# -*- coding: utf-8 -*-
"""
tools/build_micro_index.py — 本地自建微盘指数（替代 Wind 8841431.WI / 868008.WI）

背景
----
Wind 自编微盘指数受额度限制（300积分/日）无法稳定获取。
本脚本用本地 daily_basic(市值) + daily(涨跌幅) 自建，免费、可复现。

构造规则
--------
- 每日取总市值最小的 400 只，等权
- **T-1 日定成分，T 日计收益** —— 严格避免前视偏差(look-ahead bias)
- 剔除次新（上市不满 250 日）—— 时点化判据，无前视
- **ST 处理（2026-09-16 修正）**：
  默认 **不剔 ST**（无前视偏差，主口径）。
  因历史 ST 状态不可得，用"当前ST名单"剔全部历史会引入前视偏差
  （实测高估 +1.51pct/年日频、+0.77pct/年 月频）。
  如需贴近"可投资"口径可用 --exclude-st，但须声明前视偏差。

输出
----
- daily   日频调仓（近似 8841431.WI，含不可投资的再平衡溢价）
- monthly 月频调仓（近似 868008.WI，可投资口径）

实测校验（2026-09-16 修正后）
--------------------------
- 与中证2000 日相关 0.9016（对照记忆值 0.916）
- 不剔ST(无前视): 日频 17.99% / 月频 9.06%
- 剔当前ST(前视): 日频 19.50% / 月频 9.83%  ← 前视高估
- 再平衡溢价 = 日频 - 月频 ≈ 8.9pct/年（对照记忆 10-16pct）

用法
----
    python -m tools.build_micro_index                    # 全量重建（不剔ST）
    python -m tools.build_micro_index --exclude-st       # 剔当前ST（含前视偏差）
    python -m tools.build_micro_index --start 20160101
"""
import os, glob, argparse, sys

import pandas as pd
import numpy as np

# 支持直接运行（本脚本要用 common.reader，它依赖 config，需先定位代码根）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import reader

DEFAULT_DATA = r"D:\全量数据\market_data"


def build(data_dir=DEFAULT_DATA, start="20160101", out_dir=None, exclude_st=False):
    DAILY = os.path.join(data_dir, "daily")
    BASIC = os.path.join(data_dir, "daily_basic")
    META = os.path.join(data_dir, "metadata")
    out_dir = out_dir or os.path.join(data_dir, "custom_index")
    os.makedirs(out_dir, exist_ok=True)

    # 一次性读面板（两个表各读一次）
    # ⚠ 不要逐只 read_code：daily_basic/daily 已改为按年分区，逐只要对命中的
    #   每个年份文件各做一次谓词下推，5,000+ 只 → 数万次文件打开，实测 30 分钟
    #   都跑不完（旧 by_code 布局下每只只开 1 个文件，故旧写法尚可用）。
    #   改为各读一次年度文件再内存合并，只需秒级。
    print(f"[1/4] 读取全市场面板（{start} 起）...")
    b = reader.read_range("daily_basic",
                          columns=["ts_code", "trade_date", "total_mv"],
                          start_date=start, root=data_dir)
    p = reader.read_range("daily",
                          columns=["ts_code", "trade_date", "pct_chg"],
                          start_date=start, root=data_dir)
    if b.empty:
        print("  [ERROR] daily_basic 读取为空")
        return None
    print(f"      daily_basic={len(b):,}行 / daily={len(p):,}行")
    M = b.merge(p, on=["ts_code", "trade_date"], how="left")
    del b, p

    print(f"[2/4] 清洗：剔次新{' + 剔当前ST(前视)' if exclude_st else '（不剔ST，无前视）'}...")
    sb_path = os.path.join(META, "stock_basic.parquet")
    if os.path.exists(sb_path):
        sb = pd.read_parquet(sb_path)
        # 次新：基于上市日期的时点化剔除，无前视
        if "list_date" in sb.columns:
            sb2 = sb[["ts_code", "list_date"]].drop_duplicates("ts_code").copy()
            sb2["list_dt"] = pd.to_datetime(sb2["list_date"].astype(str), format="%Y%m%d", errors="coerce")
            M = M.merge(sb2[["ts_code", "list_dt"]], on="ts_code", how="left")
            M["dt"] = pd.to_datetime(M["trade_date"], format="%Y%m%d", errors="coerce")
            M = M[(M["list_dt"].isna()) | ((M["dt"] - M["list_dt"]).dt.days >= 250)]
            M = M.drop(columns=["list_dt", "dt"])
        # ST：仅当显式开启（会引入前视偏差）
        if exclude_st and "name" in sb.columns:
            st = set(sb[sb["name"].astype(str).str.contains("ST", na=False)]["ts_code"])
            M = M[~M["ts_code"].isin(st)]
            print(f"      [警告] 剔当前ST {len(st)} 只 —— 含前视偏差，实测高估约 +1.5pct/年")

    M = M.dropna(subset=["total_mv", "pct_chg"])
    M = M[M["total_mv"] > 0]
    M["ret"] = (M["pct_chg"] / 100.0).clip(-0.2, 0.2)

    dates = sorted(M["trade_date"].unique())
    g_all = dict(tuple(M.groupby("trade_date")))
    print(f"[3/4] 构造指数（{len(dates)} 个交易日）...")

    def construct(mode):
        rows, prev, cur_ym = [], None, None
        for dt in dates:
            g = g_all[dt]
            if len(g) < 500:
                continue
            if prev is None:
                prev, cur_ym = set(g.nsmallest(400, "total_mv")["ts_code"]), dt[:6]
                continue
            if mode == "monthly" and dt[:6] != cur_ym:
                cur_ym = dt[:6]
                prev = set(g.nsmallest(400, "total_mv")["ts_code"])
                continue
            sub = g[g["ts_code"].isin(prev)]
            if len(sub) < 100:
                prev = set(g.nsmallest(400, "total_mv")["ts_code"])
                continue
            rows.append((dt, sub["ret"].mean(), len(sub)))
            if mode == "daily":
                prev = set(g.nsmallest(400, "total_mv")["ts_code"])
        df = pd.DataFrame(rows, columns=["trade_date", "ret", "n"])
        df["nav"] = (1 + df["ret"]).cumprod()
        return df

    out = {}
    for mode in ["daily", "monthly"]:
        df = construct(mode)
        out[mode] = df
        yrs = (pd.to_datetime(df["trade_date"].iloc[-1]) -
               pd.to_datetime(df["trade_date"].iloc[0])).days / 365.25
        ann = df["nav"].iloc[-1] ** (1 / yrs) - 1
        vol = df["ret"].std() * np.sqrt(245)
        mdd = ((df["nav"] / df["nav"].cummax()) - 1).min()
        print(f"   [{mode:7s}] {len(df)}日 年化{ann*100:6.2f}% 波动{vol*100:5.1f}% 回撤{mdd*100:6.1f}%")
        df.to_parquet(os.path.join(out_dir, f"micro_{mode}.parquet"), index=False)

    print(f"[4/4] 已保存 → {out_dir}")
    return out


def main(argv=None):
    """CLI 入口。

    argv: 参数列表（None 则取 sys.argv[1:]），便于从统一入口 main.py 调用。
    """
    ap = argparse.ArgumentParser(
        description="本地自建微盘指数（替代 Wind 8841431.WI / 868008.WI）")
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--start", default="20160101")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--exclude-st", action="store_true",
                    help="剔除当前ST名单（含前视偏差，实测高估约+1.5pct/年，仅作敏感性对照）")
    a = ap.parse_args(argv)
    return build(a.data_dir, a.start, a.out_dir, a.exclude_st)


if __name__ == "__main__":
    main()
