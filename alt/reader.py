# -*- coding: utf-8 -*-
"""
alt/reader.py — alt 库读取与【与主库联查】
==========================================
联查的关键：**日期格式已在落盘时归一化为 'YYYYMMDD' 字符串**，
与主库完全一致，因此 merge/join 的条件天然成立，无需任何转换。

用法：
    from alt.reader import read_alt, join_main, status

    ebs = read_alt("ebs_lg")                       # 读 alt 库
    daily = read_code("daily", "000001.SZ")        # 读主库（原有函数）
    merged = join_main(ebs, daily)                 # 按日期联查

    # 低频填到高频（容差匹配）—— 无需关心传参顺序，函数自动识别高频侧
    merged = join_main(pe_monthly, daily, left_date="date",
                       right_date="trade_date", tolerance_days=400)

⚠️ 联查层三处\"不报错但结果不是你要的\"已在 2026-09-20 修复（首次真实验证时发现）：
    ① 精确匹配默认 how='left' 会静默产生大量 NaN（实证匹配率仅 3.3%）
       → 现低于 warn_min_match 时发告警
    ② 容差匹配的语义方向取决于传参顺序，反了不报错但结果完全不同
       → 现按中位日期间隔自动校正
    ③ value_on 列名写错时静默返回 None（与\"该日无数据\"无法区分）
       → 现抛 KeyError
    ④ value_on 的\"向前回退\"分支曾因取错日期列（df.columns[0]）而**恒失效**
       → 现取 spec 登记的日期列
"""
import os
import warnings
from typing import Optional

import pandas as pd

from common.reader import read_code, read_range, detect_layout, latest_date
from config_alt import ALT_DATA_DIR, TS_DATA_DIR
from alt import spec as sp_mod


# ============================================================
# 一、读取
# ============================================================
def _date_col_of(table: str, fallback: str = "date") -> str:
    """该表在 spec 中登记的日期列名（唯一真源）。

    ⚠ 不要用 `df.columns[0]` 当代日期列（2026-09-20 实测 bug）：
      列顺序由源端决定，日期列**不在第一位**是常态——
      实证 `ebs_lg` 的列是 ['沪深300指数','股债利差','股债利差均线','date']，
      日期列在**最后一列**。用 columns[0] 会解析出全量 NaT，
      导致"往前找"分支恒失效、恒返回 None（而精确命中时不暴露）。
    """
    return sp_mod.BY_NAME[table].date_col if table in sp_mod.BY_NAME else fallback


def read_alt(table: str, columns=None, start_date=None, end_date=None,
             date_col: Optional[str] = None):
    """读 alt 库某表（布局无关）。

    日期列默认取 spec 中的定义；未登记的表按 'date' 处理。
    """
    dc = date_col or _date_col_of(table)
    return read_range(table, columns=columns, start_date=start_date,
                      end_date=end_date, date_col=dc, root=ALT_DATA_DIR)


def read_alt_by_code(table: str, code: str, columns=None,
                     start_date=None, end_date=None, date_col=None,
                     code_col="ts_code"):
    """读 alt 库中某标的的数据（by_year / by_code 布局均支持）"""
    dc = date_col or _date_col_of(table)
    return read_code(table, code, columns=columns, start_date=start_date,
                     end_date=end_date, date_col=dc, code_col=code_col,
                     root=ALT_DATA_DIR)


# ============================================================
# 二、联查（核心）
# ============================================================
def _to_dt(s):
    """'YYYYMMDD' 字符串 → datetime（失败置 NaT）"""
    return pd.to_datetime(s.astype(str), format="%Y%m%d", errors="coerce")


def _median_gap_days(df: pd.DataFrame, col: str):
    """该表日期的**中位间隔天数**（衡量序列稀疏度）。

    用于容差匹配时自动判定谁是高频侧：间隔小 = 密集 = 高频。
    """
    if col not in df.columns or len(df) < 2:
        return None
    d = _to_dt(df[col]).dropna().drop_duplicates().sort_values()
    if len(d) < 2:
        return None
    return float(d.diff().dt.days.median())


def join_main(left: pd.DataFrame, right: pd.DataFrame,
              on: Optional[str] = None, how: str = "left",
              left_date: Optional[str] = None, right_date: Optional[str] = None,
              tolerance_days: Optional[int] = None,
              warn_min_match: float = 0.5):
    """把 alt 库数据与主库数据按日期联查。

    两侧日期列名可能不同（alt 用 'date'，主库用 'trade_date'），
    故分别指定后在临时列上对齐。

    参数:
        left/right:     两个 DataFrame
        on:             若两侧日期列同名，直接用它
        left_date:      左侧日期列名
        right_date:     右侧日期列名
        how:            left / inner / right / outer
        tolerance_days: 允许的日期偏差（天），用于把**低频**数据向前填充到
                        **高频**序列上。None = 精确匹配。
                        ⚠ 给定值时，函数会自动识别谁更密集并把它当作高频侧
                          （见下"方向自动识别"），无需关心传参顺序。
        warn_min_match: 精确匹配时，若左侧日期中能匹配上的比例低于此值，
                        发出告警。默认 0.5。设为 0 可关闭。

    返回:
        联查后的 DataFrame

    ⚠ 两个实测坑（2026-09-20 验证时发现，均为"不报错但结果不是你要的"）：

      ① **精确匹配默认 how='left' 会静默产生大量 NaN**。
         实证：alt 股债利差 5,209 行（2005 起）与主库某股 174 行（2026 起）
         联查 → 结果仍是 5,209 行，但**只有 174 行真正匹配、匹配率 3.3%**，
         其余 5,035 行右侧全为 NaN。无报错、有输出，极易被当成"联查成功"。
         → 对策：匹配率低于 warn_min_match 时发告警。

      ② **容差匹配的结果取决于传参顺序**，顺序反了不报错但结果完全不同。
         实证：join_main(月频, 日频) → 876 行（实为"高频填到低频"，与意图相反）；
               join_main(日频, 月频) → 174 行（才是"低频填到高频"）。
         → 对策：按中位日期间隔自动判定高频侧并校正顺序。
    """
    if left is None or len(left) == 0 or right is None or len(right) == 0:
        return pd.DataFrame()

    lc = on or left_date
    rc = on or right_date
    if lc is None or rc is None:
        raise ValueError("需指定 on 或 (left_date, right_date)")

    if lc not in left.columns:
        raise KeyError(f"左侧缺少日期列 '{lc}'（实际: {list(left.columns)[:10]}）")
    if rc not in right.columns:
        raise KeyError(f"右侧缺少日期列 '{rc}'（实际: {list(right.columns)[:10]}）")

    L = left.copy()
    R = right.copy()

    # ---------- 精确匹配 ----------
    if tolerance_days is None:
        if lc != rc:
            R = R.rename(columns={rc: lc})
        out = pd.merge(L, R, on=lc, how=how, suffixes=("", "_m"))

        # 低匹配率告警（坑 ①）
        if warn_min_match and how in ("left", "outer") and len(L):
            matched = int(L[lc].isin(set(R[lc])).sum())
            rate = matched / len(L)
            if rate < warn_min_match:
                warnings.warn(
                    f"联查匹配率偏低：{matched}/{len(L)} = {rate*100:.1f}% "
                    f"（阈值 {warn_min_match*100:.0f}%）。\n"
                    f"    结果有 {len(L)-matched} 行右侧全为 NaN —— "
                    f"两层数据的日期范围可能不重叠。\n"
                    f"    检查：左侧 {lc} 范围 "
                    f"{L[lc].min()}~{L[lc].max()} vs "
                    f"右侧 {rc} 范围 {right[rc].min()}~{right[rc].max()}；\n"
                    f"    若确实只要重叠部分，用 how='inner'（本次将返回 {matched} 行）。",
                    stacklevel=2)
        return out

    # ---------- 容差匹配（把低频数据填到高频上）----------
    # 方向自动识别（坑 ②）：密集的一侧作为 left（高频序列，结果保留其行数与日期列名）
    gl = _median_gap_days(L, lc)
    gr = _median_gap_days(R, rc)
    if gl is not None and gr is not None and gl > gr:
        warnings.warn(
            f"容差匹配方向已自动校正：左侧中位间隔 {gl:.0f} 天 > 右侧 {gr:.0f} 天，"
            f"即左侧更稀疏。\n"
            f"    已交换两侧，使**高频序列**作为结果主体"
            f"（预期行数 {len(R)}，日期列名保留 '{rc}'）。\n"
            f"    若不希望自动校正，请显式调换传参顺序。",
            stacklevel=2)
        L, R = R, L
        lc, rc = rc, lc

    L["_dt"] = _to_dt(L[lc])
    R["_dt"] = _to_dt(R[rc])
    L = L.dropna(subset=["_dt"]).sort_values("_dt")
    R = R.dropna(subset=["_dt"]).sort_values("_dt")
    out = pd.merge_asof(L, R, on="_dt", direction="backward",
                        tolerance=pd.Timedelta(days=tolerance_days),
                        suffixes=("", "_m"))
    return out.drop(columns=["_dt"])


def value_on(table: str, date: str, column: str, tolerance_days: int = 400):
    """取 alt 库某表在给定日期附近的取值（用于"某日估值水位"这类查询）。

    先精确命中；未命中则**向前回退**（找 tolerance_days 内最近的一次取值），
    用于月频表（如月度 PE）在任意交易日取值的场景。

    ⚠ 两种失败必须区分（2026-09-20 实测修正）:
      · **列名写错** → 抛 KeyError（编程错误，必须立刻暴露）。
        原实现与"该日无数据"一样返回 None，看起来像"那天没数据"。
      · **表为空 / 窗口内无数据** → 返回 None（业务情况，合理）。

    ⚠ 另修正一处日期列 bug：原实现取 `df.columns[0]` 当日期列，
      而日期列由源端列顺序决定、**不在第一位是常态**
      （实证 ebs_lg 为 ['沪深300指数','股债利差','股债利差均线','date']）。
      取错后全量 NaT → 向前回退分支**恒失效**、恒返回 None。
      现改为取 spec 登记的日期列。
    """
    dc = _date_col_of(table)

    # ① 精确命中
    df = read_alt(table, start_date=date, end_date=date)
    if len(df):
        if column not in df.columns:
            raise KeyError(
                f"列 '{column}' 不存在于表 '{table}'"
                f"（实际列: {list(df.columns)}）")
        return df[column].iloc[-1]

    # ② 向前回退
    df = read_alt(table, end_date=date)
    if len(df) == 0:
        return None                      # 表为空：业务情况
    if column not in df.columns:
        raise KeyError(
            f"列 '{column}' 不存在于表 '{table}'"
            f"（实际列: {list(df.columns)}）")

    dcol = dc if dc in df.columns else next(
        (c for c in df.columns if "date" in str(c).lower()), None)
    if dcol is None:
        return None                      # 找不到日期列，无法回退
    df = df.assign(_dt=_to_dt(df[dcol]))
    cutoff = pd.to_datetime(str(date), format="%Y%m%d") - pd.Timedelta(days=tolerance_days)
    sub = df[df["_dt"] >= cutoff].sort_values("_dt")
    if len(sub) == 0:
        return None
    return sub[column].iloc[-1]


# ============================================================
# 三、状态
# ============================================================
def status(verbose=True):
    """alt 库现状：已建表、行数、日期范围。

    返回 list[dict]，便于程序化使用。
    """
    out = []
    if not os.path.isdir(ALT_DATA_DIR):
        if verbose:
            print(f"alt 库尚不存在: {ALT_DATA_DIR}")
        return out

    for name in sorted(os.listdir(ALT_DATA_DIR)):
        d = os.path.join(ALT_DATA_DIR, name)
        if not os.path.isdir(d) or name.startswith("_"):
            continue
        files = [f for f in os.listdir(d) if f.endswith(".parquet")]
        if not files:
            continue
        dc = sp_mod.BY_NAME[name].date_col if name in sp_mod.BY_NAME else "date"
        try:
            df = read_range(name, root=ALT_DATA_DIR)
            rows = len(df)
            dr = "—"
            if dc in df.columns and rows:
                s = df[dc].astype(str)
                dr = f"{s.min()} ~ {s.max()}"
        except Exception as e:  # noqa: BLE001
            rows, dr = -1, f"读取失败: {type(e).__name__}"
        rec = {"table": name, "files": len(files), "rows": rows,
               "date_range": dr, "date_col": dc}
        out.append(rec)
        if verbose:
            print(f"  {name:<22} {len(files):>3} 文件  {rows:>8,} 行  {dr}")

    if verbose:
        if not out:
            print("  （尚无数据）")
        else:
            print(f"\n  合计 {len(out)} 张表 / {sum(r['rows'] for r in out):,} 行")
    return out


def compare_with_main(table: str, main_table: Optional[str] = None,
                      date_col: Optional[str] = None):
    """对比 alt 表与主库同名/对应表的最新日期（用于判断谁更新）。

    仅做日期维度对比，不读取全量数据。
    """
    dc = date_col or (sp_mod.BY_NAME[table].date_col if table in sp_mod.BY_NAME else "date")
    mt = main_table or table
    alt_d = latest_date(table, date_col=dc, root=ALT_DATA_DIR)
    main_d = latest_date(mt, date_col="trade_date", root=TS_DATA_DIR)
    return {"table": table, "alt_latest": alt_d, "main_latest": main_d,
            "main_table": mt}


def main():
    """CLI: python -m alt.reader"""
    import sys
    k = sys.argv[1] if len(sys.argv) > 1 else "status"
    if k == "status":
        print("=" * 74)
        print(f"alt 库现状  {ALT_DATA_DIR}")
        print("=" * 74)
        status()
    else:
        print("用法: python -m alt.reader [status]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
