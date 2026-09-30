# -*- coding: utf-8 -*-
"""
alt/io.py — alt 库落盘层（日期归一化 + 复用主库存储设施）
==========================================================
职责：
  1. 把 akshare 返回的 DataFrame **归一化**成与主库同构的形态
     （日期列 → 'YYYYMMDD' 字符串；枚举列注入；按日型注入日期）
  2. 按 spec.layout 落盘（复用 common/parquet_store 的 merge_append /
     upsert_by_year，零改动）
  3. **路径硬保护**：任何写入前校验目标在 alt 授权区，绝不触碰主库

复用说明（不重复造轮子）：
  common.parquet_store.merge_append   —— 单文件"读→concat→去重→写回"
  common.parquet_store.upsert_by_year —— 按年分区批量落盘
"""
import os
from typing import Optional

import pandas as pd

from common.paths import ensure_dir
from common.parquet_store import merge_append, upsert_by_year
from common.reader import detect_layout, read_range
from config_alt import (
    ALT_DATA_DIR,
    alt_table_dir,
    assert_writable,
    normalize_date_series,
)
from alt.spec import TableSpec


# ============================================================
# 一、归一化
# ============================================================
def resolve_subset(df: pd.DataFrame, spec: TableSpec):
    """校验去重键列是否存在；缺失则回退全列判重（并告警）。

    为什么必须校验：按年分区下一个文件含全市场，若主键列名写错（akshare
    列名可能随版本变化），会造成"同一天数据被整批删除"的静默故障。
    """
    if spec.subset in (None, "*"):
        return "*", None
    cols = spec.subset if isinstance(spec.subset, list) else [spec.subset]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        return "*", (f"去重键列缺失 {missing} → 回退全列判重（实际列: "
                     f"{list(df.columns)[:12]}）")
    return list(cols), None


def normalize(df: pd.DataFrame, spec: TableSpec,
              req_date: Optional[str] = None,
              enum_value: Optional[str] = None):
    """把原始返回归一化为落盘形态。

    返回: (df_normalized, warnings: list[str])
    """
    warns = []
    if df is None or len(df) == 0:
        return pd.DataFrame(), warns

    out = df.copy()

    # 1) 列重命名（spec.rename）
    if spec.rename:
        exist = {k: v for k, v in spec.rename.items() if k in out.columns}
        if exist:
            out = out.rename(columns=exist)

    # 2) 日期列处理
    if not spec.has_date:
        # 无日期表（如城市清单、监测点表）：不做日期处理
        pass
    elif spec.inject_date:
        # 按日型接口：源数据不含日期，用请求日期注入
        if not req_date:
            warns.append("该表需注入日期但未提供 req_date → 跳过日期列")
        else:
            out[spec.date_col] = str(req_date)
    else:
        src = spec.src_date_col or spec.date_col
        if src not in out.columns:
            warns.append(f"源日期列 '{src}' 不存在（实际列: {list(out.columns)[:12]}）"
                         f" → 跳过日期归一化")
        else:
            norm = normalize_date_series(out[src])
            bad = norm.isna().sum()
            if bad:
                warns.append(f"日期列 '{src}' 有 {bad} 行无法解析，已置空")
            out[spec.date_col] = norm
            if src != spec.date_col:
                out = out.drop(columns=[src])

    # 3) 枚举列注入（enum 模式必须，否则不同枚举值的同日数据会互相判重）
    if spec.mode == "enum" and spec.enum_col:
        out[spec.enum_col] = enum_value if enum_value is not None else ""
    elif spec.mode == "enum" and not spec.enum_col:
        warns.append("enum 模式但未指定 enum_col → 多组数据可能互相判重")

    # 3b) 强制字符串列（处理语义混杂列，避免落盘时类型推断冲突）
    #     实测：market_activity 的 value 列含 3937.0 / '75.41%' / 时间戳，
    #     首次落盘定型为 double 后，再追加含 '%' 的行会转换失败 → 分区写入失败。
    for c in (spec.force_str_cols or []):
        if c in out.columns:
            out[c] = out[c].astype(str)

    # 4) 丢弃日期列为空的行（避免落盘后无法关联）
    if spec.date_col in out.columns:
        before = len(out)
        out = out[out[spec.date_col].notna()]
        if len(out) < before:
            warns.append(f"丢弃 {before - len(out)} 行日期为空的记录")

    # 5) **本批数据自身去重**（⚠ 高价值修复，实测踩坑）
    #    背景：源端会返回重复行（实测 stock_buffett_index_lg 5,212 行中自带
    #    13 行重复、stock_a_below_net_asset 自带 7 行），而复用的
    #    common.parquet_store.merge_append 有两条路径：
    #      · 新文件路径 → 直接 to_parquet，**不去重** → 源端重复直接入库
    #      · 已存在路径 → 去重后算 added，**added<=0 时直接 return 不写盘**
    #    两者叠加的后果：首次写入把重复带进去之后，后续每次更新算出的 added
    #    都是负值 → **该表此后永远不再写盘，静默冻结**（不报错、日志显示 ok）。
    #    故必须在**进入落盘前**就保证每批数据自身无重复，
    #    使 added 的计算不受源端重复干扰。
    sub, _ = resolve_subset(out, spec)
    if len(out):
        before = len(out)
        if sub == "*":
            out = out.drop_duplicates(keep="last")
        else:
            cols = [c for c in sub if c in out.columns]
            if len(cols) == len(sub):
                out = out.drop_duplicates(subset=cols, keep="last")
        if len(out) < before:
            warns.append(f"本批数据去重：{before} → {len(out)} 行"
                         f"（源端重复行，已按 {sub} 去重）")

    return out.reset_index(drop=True), warns


# ============================================================
# 二、落盘
# ============================================================
def table_path(spec: TableSpec, req_date: Optional[str] = None, year: Optional[str] = None):
    """目标文件路径（single → {table}/{table}.parquet；by_year → {table}/{YYYY}.parquet）"""
    d = alt_table_dir(spec.name)
    if spec.layout == "by_year":
        y = year or (str(req_date)[:4] if req_date else None)
        if not y:
            raise ValueError(f"{spec.name}: by_year 布局需要 req_date 或 year")
        return os.path.join(d, f"{y}.parquet")
    return os.path.join(d, f"{spec.name}.parquet")


def save(df: pd.DataFrame, spec: TableSpec, req_date: Optional[str] = None,
         dry_run: bool = False):
    """按 spec 落盘。dry_run=True 时只做校验与预演，不写任何文件。

    返回: dict(ok, rows, added, path, errors, warnings, dry_run)

    ⚠ 硬性前置条件：spec.date_col 必须在 df 中存在。
       若源日期列名与 spec.src_date_col 不符（akshare 改列名/猜错），
       日期列不会被归一化 → 落盘后无日期列 → 下游读取静默失败。
       因此这里必须**报错而非告警**（实测踩坑：曾出现"文件写成了但
       日期列名是『时间』不是『date』"的静默故障）。
    """
    result = {"table": spec.name, "ok": False, "rows": 0, "added": 0,
              "path": "", "errors": [], "warnings": [], "dry_run": dry_run}

    if df is None or len(df) == 0:
        result["warnings"].append("空数据，跳过")
        return result

    result["rows"] = len(df)

    # ── 硬校验：日期列必须存在（否则数据不可用）─────────────────
    # 例外：spec.has_date=False 的表本就没有日期列（如城市清单），跳过校验
    if spec.has_date and spec.date_col not in df.columns:
        hint = f"（spec.src_date_col={spec.src_date_col!r}）"
        result["errors"].append(
            f"日期列 '{spec.date_col}' 在落盘数据中不存在{hint} "
            f"→ 拒绝写入。实际列: {list(df.columns)[:12]}"
            f"\n    修复：核对 akshare 返回的实际日期列名，更正 spec.src_date_col"
            f"；若该表本无日期列，应设 has_date=False"
        )
        return result

    subset, warn = resolve_subset(df, spec)
    if warn:
        result["warnings"].append(warn)

    # 无日期表：强制走 single 布局，直接合并写回
    if not spec.has_date:
        target = table_path(spec)
        result["path"] = target
        if dry_run:
            result["ok"] = True
            result["warnings"].append("[dry-run] 将写入单文件（无日期表）")
            return result
        assert_writable(target)
        ensure_dir(os.path.dirname(target))
        changed, added = merge_append(target, df, subset=subset)
        result.update(ok=True, added=added)
        return result

    if spec.layout == "by_year":
        years = df[spec.date_col].astype(str).str[:4].unique()
        target_dir = alt_table_dir(spec.name)
        target = table_path(spec, req_date=req_date, year=years[0])
        result["path"] = target_dir + os.sep + "{YYYY}.parquet"
        if dry_run:
            result["ok"] = True
            result["warnings"].append(
                f"[dry-run] 将写入 {len(years)} 个年度分区: {sorted(years)}")
            return result
        # 路径硬保护
        assert_writable(target)
        ensure_dir(target_dir)
        sort_by = subset[0] if isinstance(subset, list) else spec.date_col
        st = upsert_by_year(df, target_dir, date_col=spec.date_col,
                            subset=subset, sort_by=sort_by)
        # ⚠ 必须检查 fail：upsert_by_year 遇到分区写入异常会**捕获并计数**，
        #   若此处只看 added 就会"写入失败但报 OK"（实测：market_activity 的
        #   value 列混有 '75.41%' 字符串导致分区转换失败，却仍返回 OK）。
        n_fail = st.get("fail", 0)
        if n_fail:
            result["errors"].append(
                f"按年分区写入失败 {n_fail} 个（共 {st.get('partitions',0)} 成功）"
                f" → 数据未完整落盘，请检查列类型是否一致")
            return result
        result.update(ok=True, added=st.get("added", 0))
        return result

    # single 布局
    target = table_path(spec)
    result["path"] = target
    if dry_run:
        if spec.date_col not in df.columns:
            result["errors"].append(
                f"[dry-run] 日期列 '{spec.date_col}' 不存在 → 实跑时会被拒绝")
            return result
        result["ok"] = True
        result["warnings"].append(f"[dry-run] 将写入单文件 {os.path.basename(target)}")
        return result
    assert_writable(target)
    ensure_dir(os.path.dirname(target))
    sort_by = spec.date_col if spec.date_col in df.columns else None
    changed, added = merge_append(target, df, subset=subset, sort_by=sort_by)
    result.update(ok=True, added=added)
    return result


# ============================================================
# 三、读取（统一入口在 alt.reader，此处仅保留布局/清单工具）
# ============================================================
def read_alt(*args, **kwargs):
    """【已废弃 · 委托】读取统一走 alt.reader.read_alt。

    历史上 io 与 reader 各有一份同功能实现（前者直读、后者是主入口），
    易造成"改一处漏一处"。现收敛为单一实现：本函数仅做转发，
    保留它是为了不破坏可能存在的旧调用方。
    """
    from alt.reader import read_alt as _read
    return _read(*args, **kwargs)


def alt_layout(table: str) -> str:
    """alt 库某表的布局（none=尚未建）"""
    return detect_layout(table, root=ALT_DATA_DIR)


def alt_tables() -> list:
    """alt 库中已存在的表（目录名）"""
    if not os.path.isdir(ALT_DATA_DIR):
        return []
    out = []
    for n in sorted(os.listdir(ALT_DATA_DIR)):
        d = os.path.join(ALT_DATA_DIR, n)
        if os.path.isdir(d) and not n.startswith("_"):
            if any(f.endswith(".parquet") for f in os.listdir(d)):
                out.append(n)
    return out
