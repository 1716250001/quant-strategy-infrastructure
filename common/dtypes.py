# -*- coding: utf-8 -*-
"""common/dtypes.py — 落盘前的类型归一（数据源无关）

为什么单独成文件（DB-R1，2026-10-28）:
  该函数原在 alt/io.py，服务于 akshare 外盘（qfq_factor 返回 str）。
  tushare 侧 moneyflow_hsgt 有**同构问题**（6 个数值列返回 str），但
  fetch/ 不应反向依赖 alt/（主库 ↔ 备用库物理隔离，备用库须可剥离）。
  故上移到 common/，fetch 与 alt 各自单向依赖 common。
"""
import pandas as pd


def coerce_float_cols(df: pd.DataFrame, cols) -> pd.DataFrame:
    """把指定列强制转为 float（转不动置 NaN，由后续判空/告警兜底）。

    参数:
        df:   目标 DataFrame（**不原地修改**，返回新对象）
        cols: 需转 float 的列名列表（None/空 = 原样返回）
    返回:
        转换后的 DataFrame

    ⚠ 为什么必须转（两条实测依据，勿删）:
      ① 不转 ⇒ 下游 `close × factor`、`df[col].sum()` 直接抛类型错误
         或退化为字符串拼接（object 列 sum 会静默拼接）；
      ② 跨批 schema 不稳定（一批 str、一批 float）⇒ 按年分区写入时
         `_write_aligned` 的类型对齐会失败，进而引入 schema 漂移。
    """
    if df is None or len(df) == 0 or not cols:
        return df
    out = df
    for c in cols:
        if c in out.columns:
            if out is df:
                out = df.copy()
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out
