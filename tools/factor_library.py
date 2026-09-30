# -*- coding: utf-8 -*-
"""
tools/factor_library.py — 因子库（全收益基准 + 风格因子）

用途
----
为公募基金风格/α分析提供标准化因子集。所有因子均为本地可得，无需外部API。

因子集
------
【规模基准·全收益版】（P1-1 修复：价格指数少算分红，须用全收益）
    H00300.CSI        沪深300全收益
    H00905.CSI        中证500全收益
    H00852.CSI        中证1000全收益
    932000CNY010.CSI  中证2000全收益
    399606.SZ         创业板R（全收益）
    自建微盘月频        custom_index/micro_monthly.parquet

【风格因子】
    000919.CSI   300价值
    000918.CSI   300成长
    000803.CSI   300波动（低波）
    H00922.CSI   中证红利全收益
    930855.CSI   CS质量成长
    H20260.CSI   300动量全收益（2013-11起）

关键结论（2026-09-16 实测）
-------------------------
1. 全收益化效果：α下降 1.52pct（非正交口径）
2. **对称正交化会抹掉该效果**（仅降0.07pct）→ 已弃用对称正交化
3. 最终方案：**非正交多元回归**（OLS共线性下α仍无偏，β可解释）
4. 风格分类用**相关系数法**（不受共线性影响）

用法
----
    from tools.factor_library import load_factors
    F, meta = load_factors()            # 全收益规模基准
    F2, meta2 = load_factors(style=True)  # + 风格因子
"""
import os
import glob
import argparse
import pandas as pd
import numpy as np

from common import reader

DEFAULT_DATA = r"D:\全量数据\market_data"

SIZE_FULL = {
    "沪深300R": "H00300.CSI",
    "中证500R": "H00905.CSI",
    "中证1000R": "H00852.CSI",
    "中证2000R": "932000CNY010.CSI",
    "创业板R": "399606.SZ",
}
STYLE = {
    "价值": "000919.CSI",
    "成长": "000918.CSI",
    "低波": "000803.CSI",
    "红利R": "H00922.CSI",
    "质量": "930855.CSI",
    "动量R": "H20260.CSI",
}


def _load_index(code, data_dir):
    """读指数日线收益率序列。

    index_daily 已迁移为按年分区，不能再按 {code}.parquet 定位文件
    （旧写法会静默返回 None，导致因子库整体为空）。
    """
    d = reader.read_code("index_daily", code,
                         columns=["trade_date", "close"], root=data_dir)
    if d is None or d.empty:
        return None
    d = d.dropna()
    d["trade_date"] = d["trade_date"].astype(str)
    s = d.sort_values("trade_date").set_index("trade_date")["close"].pct_change().dropna()
    return s[s.abs() < 0.25]


def _load_micro(data_dir):
    p = os.path.join(data_dir, "custom_index", "micro_monthly.parquet")
    if not os.path.exists(p):
        return None
    d = pd.read_parquet(p)
    d["trade_date"] = d["trade_date"].astype(str)
    s = d.set_index("trade_date")["ret"]
    return s[s.abs() < 0.25]


def load_factors(style=False, data_dir=DEFAULT_DATA):
    """返回 (因子DataFrame, 元信息dict)"""
    out, meta = {}, {}
    for k, c in SIZE_FULL.items():
        s = _load_index(c, data_dir)
        if s is not None:
            out[k] = s
            meta[k] = {"code": c, "type": "规模-全收益"}
    m = _load_micro(data_dir)
    if m is not None:
        out["微盘"] = m
        meta["微盘"] = {"code": "self-built", "type": "规模-自建月频"}
    if style:
        for k, c in STYLE.items():
            s = _load_index(c, data_dir)
            if s is not None:
                out[k] = s
                meta[k] = {"code": c, "type": "风格"}
    F = pd.DataFrame(out).dropna()
    return F, meta


def regress(fund_ret, F, orth=False):
    """非正交多元回归（推荐）。orth=True 时用对称正交化（不推荐，见文档）"""
    if orth:
        X = F.values
        mu, sd = X.mean(0), X.std(0)
        Xs = (X - mu) / sd
        C = np.corrcoef(Xs.T)
        w, V = np.linalg.eigh(C)
        w = np.maximum(w, 1e-10)
        F = pd.DataFrame(Xs @ (V @ np.diag(w ** -0.5) @ V.T), index=F.index, columns=F.columns)
    j = pd.concat([fund_ret.rename("f"), F], axis=1, join="inner").dropna()
    if len(j) < 250:
        return None
    fc = list(F.columns)
    X = np.column_stack([np.ones(len(j))] + [j[k].values for k in fc])
    y = j["f"].values
    cf, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ cf
    n, k = X.shape
    s2 = resid @ resid / (n - k)
    se = np.sqrt(s2 * np.linalg.inv(X.T @ X)[0, 0])
    return {
        "alpha_comp": (1 + cf[0]) ** 245 - 1,   # 复利年化（主口径）
        "alpha_lin": cf[0] * 245,               # 线性年化（对照）
        "t": cf[0] / se, "r2": 1 - np.var(resid) / np.var(y), "n": len(j),
        "betas": {k: cf[1 + i] for i, k in enumerate(fc)},
        "cors": {k: float(j["f"].corr(j[k])) for k in fc},
    }


def main(argv=None):
    """CLI 入口：打印因子矩阵概览、因子清单与相关矩阵。

    argv: 参数列表（None 则取 sys.argv[1:]），便于从统一入口 main.py 调用。
          --style   是否含风格因子（默认含）
          --matrix  是否打印完整相关矩阵（默认打印）
    """
    ap = argparse.ArgumentParser(
        description="因子库（全收益基准 + 风格因子）")
    ap.add_argument("--style", action="store_true", default=True,
                    help="含风格因子（默认含）")
    ap.add_argument("--no-style", dest="style", action="store_false")
    ap.add_argument("--no-matrix", dest="matrix", action="store_false",
                    default=True, help="不打印相关矩阵，只看清单")
    a = ap.parse_args(argv)

    F, meta = load_factors(style=a.style)
    if F is None or getattr(F, "empty", True):
        print("[WARN] 因子矩阵为空，请先准备基准指数与微盘指数数据")
        return None
    print(f"因子矩阵: {F.shape} | {F.index[0]} → {F.index[-1]}")
    for k, v in meta.items():
        print(f"  {k:<10} {v['code']:<18} {v['type']}")
    if a.matrix:
        print("\n相关矩阵:")
        print(F.corr().round(3).to_string())
    return F


if __name__ == "__main__":
    main()
