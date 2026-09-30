# -*- coding: utf-8 -*-
"""
alt/verify_spec.py — 表规格校验工具
====================================
用途：在批量建库【之前】，逐表试调一次，核对 spec 假设与 akshare 实际返回是否一致。

为什么需要它：
    spec.py 里的 src_date_col / subset / enum_param 都是**人工假设**。
    若假设错（如实际列名是「时间」而非「日期」），落盘时会变成
    "文件写了但日期列不对"的静默故障。本工具把这类错误在**建库前**暴露。

校验项：
    1. 接口可调用性（函数存在 + 能返回数据）
    2. src_date_col 是否存在（按日型接口跳过）
    3. subset 各列是否存在（"*" 跳过）
    4. enum_param 各取值是否都能返回数据、返回是否为空
    5. 日期列可解析性 + 归一化后形态
    6. 自动推荐正确的日期列名（逐列试解析日期）

请求预算：
    每次调用 1 个请求；enum 表按枚举值数量计。
    默认只跑每个枚举值的**首个**取值用于探测（--full-enum 可全跑）。

CLI：
    python -m alt.verify_spec --tier P0            # 校验 P0
    python -m alt.verify_spec --only gxl_lg        # 单表
    python -m alt.verify_spec --tier P0 --full-enum  # enum 全取值
    python -m alt.verify_spec --list-todo          # 只列出待校验表（零请求）
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

import pandas as pd

from config_alt import (ALT_META_DIR, assert_writable, ensure_alt_dirs, ALT_TMP_DIR)
from alt.rate import AltRateLimiter
from alt.spec import ALL_SPECS, BY_TIER, TIERS, get as spec_get

REPORT_FILE = os.path.join(ALT_META_DIR, "spec_verify_report.json")

# 日期解析试探：匹配 8 位 / 带分隔符的日期串
_DATE_PAT = re.compile(r"^\d{4}[-/]?\d{2}[-/]?\d{2}")


def guess_url(func_name: str) -> str:
    """转发到唯一真源 alt/urls.py（R5，2026-09-19 修复）。

    本函数原是与 backfill.guess_url 并列的**第二份实现**，注释自称
    "保持一致"，实际规则在三处不同（宏观前缀用 macro_ 还是逐国列举、
    _lg 是 endswith 还是包含、qvix 判定条件），导致同一接口在两个模块
    被判入不同限流档位。现统一转发，杜绝漂移。
    """
    from alt.urls import guess_url as _guess
    return _guess(func_name)


def _recent_trade_date(max_days_back: int = 10) -> str:
    """取"距今 max_days_back 天内"的最近已收盘交易日。

    用于按日型接口的探测：既要落在源端保留窗口内，又要是已收盘的交易日。
    直接复用 backfill.latest_trade_date（它已处理收盘保护），
    若窗口很短则回退到更早的交易日。
    """
    from alt.backfill import latest_trade_date
    from common.calendar import open_dates, recent_trade_dates

    t = latest_trade_date()
    if max_days_back <= 0:
        return t
    # 从"最近 N 天窗口"内选一个交易日（避开窗口最边缘，取窗口内偏近端）
    dates = recent_trade_dates(max_days_back, end_date=t)
    return dates[-1] if dates else t


def _looks_like_date(v) -> bool:
    """判断单个值是否像日期（而非大数字）。

    ⚠ 为什么不能只用正则：'1234567890'（成交额）也能匹配
    ^\\d{4}...\\d{2}...\\d{2}，实测导致「成交额/流通市值/总市值」
    被误判为日期列。必须加上**年份合理性**与**去分隔符后长度**约束。
    """
    s = str(v).strip()
    digits = re.sub(r"[-/\.\s]", "", s)
    if not digits.isdigit():
        return False
    # 允许 YYYYMMDD(8) / YYYYMMDDHHMMSS(14) / YYYYMM(6)
    if len(digits) not in (6, 8, 12, 14):
        return False
    year = int(digits[:4])
    if not (1990 <= year <= 2100):
        return False
    if len(digits) >= 8:
        mm, dd = int(digits[4:6]), int(digits[6:8])
        if not (1 <= mm <= 12 and 1 <= dd <= 31):
            return False
    return True


def date_like_columns(df: pd.DataFrame, max_rows=50):
    """逐列试探：哪些列看起来是日期列（含年份合理性校验）"""
    out = []
    for c in df.columns:
        s = df[c].head(max_rows)
        hit = s.map(_looks_like_date).mean()
        if hit > 0.8:
            out.append({"column": str(c), "match_ratio": round(float(hit), 3),
                        "sample": str(df[c].iloc[0])[:24], "dtype": str(df[c].dtype)})
    return out


def can_parse_dates(s: pd.Series, min_ratio: float = 0.8) -> tuple:
    """尝试把列解析为日期。

    返回 (可接受?, 归一化样例, 非空占比, 说明)

    注意：min_ratio 只控制"是否可接受"，但**部分缺失本身要报出来**，
    因为日期缺失会在落盘时被丢弃（normalize 会 dropna 日期列），
    若占比过高则等于静默丢数据。
    """
    try:
        d = pd.to_datetime(s.astype(str).str.strip(), errors="coerce")
        ratio = float(d.notna().mean())
        ok = ratio >= min_ratio
        sample = d.dropna().iloc[0].strftime("%Y%m%d") if d.notna().any() else ""
        note = ""
        if ratio < 1.0:
            note = f"日期非空 {d.notna().sum()}/{len(d)}（{ratio:.0%}）"
        return bool(ok), sample, ratio, note
    except Exception:
        return False, "", 0.0, "解析异常"


def check_freshness(df: pd.DataFrame, date_col: str,
                    max_age_days: int = 120, today=None) -> dict:
    """数据新鲜度检测。

    ⚠ 为什么必须查：接口"能调用、有数据、格式正确"≠ 数据是**当期**的。
    实测踩坑（2026-09-18）：akshare 的金十系美国宏观接口全部返回，
    结构也正常，但最新数据停在 **2025-08**（陈旧约 1 年）——
    属源端停更。不查新鲜度就会拿一年前的数据做当期判断。

    max_age_days 默认 120 天：宏观数据有发布滞后（月度+季度），
    留足余量；股票/行情类应设更小（如 15 天）。
    """
    today = today or pd.Timestamp.now().normalize()
    out = {"checked": False, "stale": False, "latest": "", "age_days": None}
    if date_col not in df.columns or len(df) == 0:
        return out
    # 排除未来日期（结构性占位行，如"下月待公布"）
    d = pd.to_datetime(df[date_col].astype(str), errors="coerce").dropna()
    if len(d) == 0:
        return out
    past = d[d <= today]
    latest = past.max() if len(past) else d.max()
    age = int((today - latest).days)
    out.update(checked=True, latest=latest.strftime("%Y%m%d"), age_days=age,
               stale=age > max_age_days,
               future_rows=int((d > today).sum()))
    return out


def check_value_sanity(df: pd.DataFrame, skip_cols=None, core_cols=None,
                       date_col=None) -> tuple:
    """数值健全性检测，返回 (problems, warnings)。

    ⚠ 校准说明（实测两轮才调准）：
      第一轮过严 —— 把「炸板次数 零值 37%」「涨速 常数 0.0」判为损坏，
      但这两者**业务上完全正常**（多数涨停股不炸板；特定池的涨速字段
      本就为 0），造成大批假警报。

      校准后按**信号强度**分两级：
        problem（阻断）：**最新非空值为 0 且历史区间出现过非零**
                         —— 强信号。实测 qvix_50index 正是此形态
                         （最新连续 3 日 = 0.0，历史 0~36.89）。
        warning（提示）：大量零值 / 整列常数
                         —— 弱信号，可能是业务正常（稀疏计数、占位字段），
                         交人工判断，不阻断。

    参数:
        core_cols: 只对这些列做 problem 级判定（未给则对所有数值列）。
                   用于避免把辅助字段的 0 值当成损坏。
        date_col:  日期列名。给定且可解析时，取"最新值"前会先按日期排序
                   （R6，2026-09-19 修复）；不给则退化为原始行序取末行。
    """
    problems, warnings = [], []
    skip = set(skip_cols or []) | {"date", "trade_date", "datetime", "time",
                                   "symbol", "period", "商品", "指数", "item",
                                   "序号", "代码", "名称"}

    # ⚠ R6（2026-09-19 修复）：取"最新值"前必须显式按日期排序。
    #   原实现直接 nn.iloc[-1]，取的是**原始行序的末行**，而不是最新日期的值。
    #   若接口返回倒序（主库已有同类踩坑：fund_daily 需先 sort_values），
    #   iloc[-1] 拿到的是**最早**的值 → "最新非空值为 0 = 损坏"的判定会误判。
    #   实测：QVIX 的损坏检测目前"碰巧正确"，说明该源此刻是升序——
    #   属隐患而非活跃 bug，但不该依赖运气。
    work = df
    if date_col and date_col in df.columns:
        try:
            _ord = pd.to_datetime(df[date_col].astype(str), errors="coerce")
            if _ord.notna().sum() >= 3:
                work = (df.assign(**{"__ord__": _ord})
                          .sort_values("__ord__")
                          .drop(columns=["__ord__"]))
        except Exception:  # noqa: BLE001
            work = df

    for c in work.columns:
        if str(c) in skip:
            continue
        s = pd.to_numeric(work[c], errors="coerce")
        if s.notna().sum() < 3:
            continue
        nn = s.dropna()
        n_zero = int((nn == 0).sum())
        zero_ratio = n_zero / len(nn)
        last = nn.iloc[-1]
        uniq = nn.nunique()
        has_nonzero = bool((nn != 0).any())

        # 强信号 → problem
        if last == 0 and has_nonzero and (core_cols is None or str(c) in core_cols):
            problems.append({"column": str(c), "kind": "最新值为零",
                             "detail": f"最新非空值 = 0，历史区间 "
                                       f"[{nn.min()}, {nn.max()}]"})
            continue

        # 弱信号 → warning
        if uniq == 1:
            warnings.append({"column": str(c), "kind": "常数",
                             "detail": f"全列唯一值 = {last}（可能是业务正常）"})
        elif zero_ratio > 0.2:
            warnings.append({"column": str(c), "kind": "大量零值",
                             "detail": f"零值 {n_zero}/{len(nn)}"
                                       f"（{zero_ratio:.0%}）（稀疏计数属正常，请人工判断）"})
    return problems, warnings


def verify_one(sp, limiter: AltRateLimiter, full_enum=False, verbose=True):
    """校验单张表的规格。只读，不落盘。"""
    import akshare as ak

    rep = {
        "table": sp.name, "func": sp.func, "tier": sp.tier,
        "mode": sp.mode, "layout": sp.layout,
        "requests": 0, "ok": True,
        "problems": [], "suggestions": [], "warnings": [], "observations": {},
    }
    fn = getattr(ak, sp.func, None)
    if fn is None:
        rep["ok"] = False
        rep["problems"].append(f"akshare 无此函数: {sp.func}")
        return rep

    url = guess_url(sp.func)

    # ── 取样本 ──
    if sp.mode == "enum":
        vals = sp.enum_values if full_enum else sp.enum_values[:1]
        got = {}
        for v in vals:
            raw, ok, err = limiter.call(lambda vv=v: fn(**{sp.enum_param: vv}), url)
            rep["requests"] += 1
            if not ok:
                rep["problems"].append(f"enum值 {v!r} 调用失败: {err}")
                continue
            got[v] = raw
            if raw is None or len(raw) == 0:
                rep["problems"].append(f"enum值 {v!r} 返回空数据")
        if not got:
            rep["ok"] = False
            return rep
        first_v = list(got.keys())[0]
        df = got[first_v]
        rep["observations"]["enum_probe"] = {
            "probed": list(got.keys()),
            "rows_by_value": {k: int(len(v)) for k, v in got.items()},
            "all_same_cols": len({tuple(map(str, v.columns)) for v in got.values()}) == 1,
        }
    else:
        # ⚠ 按日型表（inject_date）必须传目标日期：
        #   akshare 这类接口的 date 参数默认值往往是**硬编码的历史日期**
        #   （如 stock_zt_pool_em 默认 '20241008'），不传就会拿到过期数据
        #   而误判为"返回空数据"。实测踩坑：6 张涨停族表全部误报空数据。
        kw = dict(sp.params)
        probe_date = None
        if sp.date_param:
            probe_date = _recent_trade_date(sp.max_backfill_days or 10)
            kw[sp.date_param] = probe_date
        raw, ok, err = limiter.call(lambda: fn(**kw), url)
        rep["requests"] += 1
        if probe_date:
            rep["observations"]["probe_date"] = probe_date
        if not ok:
            rep["ok"] = False
            rep["problems"].append(f"调用失败: {err}")
            return rep
        df = raw

    if df is None or len(df) == 0:
        if sp.inject_date:
            # 按日型快照：特定日子可能确实无数据（实测跌停股池
            # 20260918 = 0 行、20260915 = 27 行，属真实的行情状态），
            # 故降级为提示而非阻断。
            rep["warnings"].append(
                "该日返回空数据——快照型接口在特定行情下可能本就为空"
                "（如无跌停股），属正常；请用其他交易日复核")
            rep["observations"]["empty_ok"] = True
            return rep
        rep["ok"] = False
        rep["problems"].append("返回空数据")
        return rep

    cols = [str(c) for c in df.columns]
    rep["observations"]["rows"] = int(len(df))
    rep["observations"]["columns"] = cols
    rep["observations"]["dtypes"] = {str(c): str(df[c].dtype) for c in df.columns}

    # ── 校验 1：日期列 ──
    dl = date_like_columns(df)
    rep["observations"]["date_like_columns"] = dl

    if sp.inject_date:
        # 按日型：源数据不应有日期列（有也无妨，但落盘用的是注入的）
        rep["observations"]["inject_date"] = True
        if dl:
            rep["suggestions"].append(
                f"该表标记为按日型(注入日期)，但源数据含疑似日期列 {[d['column'] for d in dl]}；"
                f"若源数据本身带日期，应改为 src_date_col 而非注入")
    else:
        if sp.src_date_col and sp.src_date_col not in cols:
            rep["ok"] = False
            cand = [d["column"] for d in dl]
            rep["problems"].append(
                f"src_date_col={sp.src_date_col!r} 不存在。实际列: {cols[:14]}")
            if cand:
                rep["suggestions"].append(
                    f"疑似日期列为 {cand} → 建议 src_date_col 改为 {cand[0]!r}")
            else:
                rep["suggestions"].append(
                    "未发现明显日期列 → 需人工确认该接口的日期字段名")
        else:
            okp, sample, ratio, note = can_parse_dates(df[sp.src_date_col])
            rep["observations"]["src_date_sample"] = sample
            rep["observations"]["src_date_fill_ratio"] = round(ratio, 3)
            if not okp:
                rep["ok"] = False
                rep["problems"].append(
                    f"src_date_col={sp.src_date_col!r} 日期可解析率过低"
                    f"（{ratio:.0%} < 80%）{note} → 落盘会丢弃这部分行，"
                    f"建议核实数据源或弃用该表")
            elif ratio < 1.0:
                # 部分缺失：不算失败，但必须报出来（避免静默丢行）
                rep["warnings"].append(
                    f"日期列存在缺失：{note}，落盘时这部分行会被丢弃")

    # ── 校验 2：去重键 ──
    # ⚠ 注意：subset 中的某些列是**由本工程注入的**，原始返回里本就不存在：
    #     - spec.date_col：由 normalize() 从 src_date_col 归一化而来
    #     - spec.enum_col：由 normalize() 按枚举值注入
    #   这两类必须排除，否则会把正确的规格误报为"列缺失"。
    if isinstance(sp.subset, list):
        injected = {sp.date_col}
        if sp.mode == "enum" and sp.enum_col:
            injected.add(sp.enum_col)
        missing = [c for c in sp.subset if c not in cols and c not in injected]
        if missing:
            rep["ok"] = False
            rep["problems"].append(
                f"subset 列缺失: {missing}（实际列: {cols[:14]}）\n"
                f"    注：已排除注入列 {sorted(injected)}")
            code_cand = [c for c in cols
                         if any(k in str(c) for k in ("代码", "code", "股票"))]
            if code_cand:
                rep["suggestions"].append(
                    f"疑似标的列 {code_cand[0]!r} → subset 可改为 "
                    f"[{sp.date_col!r}, {code_cand[0]!r}]")

    # ── 校验 3：enum 组间结构一致性（⚠ 高价值：实测踩过坑）──
    # akshare 某些接口的不同枚举值返回**结构与频率都不同**。
    # 实测 stock_market_pe_lg：上证/深证/创业板为月频（指数+平均市盈率），
    # 而「科创版」为日频（总市值+市盈率）—— 混在一张表里会造成
    # "同表不同频率、不同列"的下游陷阱，且**不报错**。
    if sp.mode == "enum" and len(got) >= 2:
        from collections import Counter
        freq_map = {}
        cols_map = {}
        for v, frame in got.items():
            f = frame.copy()
            # 推断频率
            dcand = [c for c in f.columns
                     if str(c).lower() in ("date", "日期", "trade_date", "时间")]
            if dcand:
                dt = pd.to_datetime(f[dcand[0]].astype(str), errors="coerce").dropna()
                if len(dt) > 2:
                    med = dt.sort_values().diff().dt.days.dropna().median()
                    freq_map[str(v)] = ("月频" if med > 20
                                        else ("日频" if med < 5 else "周频"))
                else:
                    freq_map[str(v)] = "样本不足"
            # 非空列签名
            nn = sorted(str(c) for c in f.columns
                        if c not in ("date", "日期", "time") and f[c].notna().any())
            cols_map[str(v)] = tuple(nn)

        if len(set(freq_map.values())) > 1:
            rep["ok"] = False
            rep["problems"].append(
                f"【enum 组间频率不一致】{freq_map} → 混在一张表会造成"
                f"下游\"同表不同频率\"陷阱，应为异常组单独建表")
        if len(set(cols_map.values())) > 1:
            rep["ok"] = False
            sig = {}
            for v, c in cols_map.items():
                sig.setdefault(c, []).append(v)
            rep["problems"].append(
                f"【enum 组间列不一致】{ {k: v for k, v in sig.items()} } → "
                f"不同组应有不同列，建议拆分或核实口径")
        rep["observations"]["enum_freq"] = freq_map
        rep["observations"]["enum_nonnull_cols"] = {k: list(v) for k, v in cols_map.items()}

    # ── 校验 4：数据新鲜度（⚠ 高价值：实测金十系源停更 1 年）──
    if not sp.inject_date:
        fr = check_freshness(df, sp.src_date_col or sp.date_col,
                             max_age_days=sp.max_age_days)
        rep["observations"]["freshness"] = fr
        if fr["checked"] and fr["stale"]:
            rep["ok"] = False
            rep["problems"].append(
                f"【数据陈旧】最新 {fr['latest']}，距今 {fr['age_days']} 天"
                f"（阈值 {sp.max_age_days} 天）→ 源端可能已停更，"
                f"不可用于当期判断")
        elif fr["checked"] and fr["age_days"] is not None and fr["age_days"] > 30:
            rep["warnings"].append(
                f"数据最新 {fr['latest']}（距今 {fr['age_days']} 天），"
                f"确认是否符合该指标发布节奏")

    # ── 校验 5：数值健全性（强信号阻断 / 弱信号提示）──
    #   传 date_col：让"最新值"判定先按日期排序（R6），不依赖源端返回顺序
    sanc_p, sanc_w = check_value_sanity(df, core_cols=sp.core_metric_cols or None,
                                        date_col=sp.src_date_col or sp.date_col)
    rep["observations"]["value_sanity"] = {
        "problems": sanc_p or "clean",
        "warnings": sanc_w or "clean",
    }
    for it in sanc_p:
        rep["ok"] = False
        rep["problems"].append(
            f"【数值可疑·{it['kind']}】列 {it['column']!r}：{it['detail']}"
            f" → 核实源端是否已损坏")
    for it in sanc_w:
        rep["warnings"].append(
            f"数值需人工判断·{it['kind']}：列 {it['column']!r}：{it['detail']}")

    # ── 校验 6：enum 列 ──
    if sp.mode == "enum":
        if not sp.enum_col:
            rep["problems"].append("enum 模式未指定 enum_col → 多组数据会互相判重")
        else:
            rep["observations"]["enum_injected"] = sp.enum_col

    # ── 判定 ──
    if rep["problems"]:
        rep["ok"] = False
    return rep


def run(tier=None, only=None, full_enum=False, json_out=True):
    ensure_alt_dirs()
    seps = [spec_get(only)] if only else (list(BY_TIER.get(tier, [])) if tier else list(ALL_SPECS))
    if not seps:
        print(f"无匹配表（tier={tier}, only={only}）")
        return []

    limiter = AltRateLimiter(verbose=True)
    print("=" * 84)
    print("规格校验（只读，不落盘）")
    print(f"  待校验: {len(seps)} 张    时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 84)

    reports = []
    t0 = time.time()
    for i, sp in enumerate(seps, 1):
        print(f"\n[{i}/{len(seps)}] {sp.name:<22} {sp.func}")
        try:
            r = verify_one(sp, limiter, full_enum=full_enum)
        except Exception as e:  # noqa: BLE001
            r = {"table": sp.name, "func": sp.func, "tier": sp.tier, "mode": sp.mode,
                 "layout": sp.layout, "requests": 0, "ok": False,
                 "problems": [f"校验异常: {type(e).__name__}: {e}"],
                 "suggestions": [], "observations": {}}
        reports.append(r)
        status = "OK" if r["ok"] else "问题"
        obs = r.get("observations", {})
        fr = obs.get("freshness", {})
        frs = ""
        if fr.get("checked"):
            frs = (f" 新鲜度={fr['latest']}({fr['age_days']}天前)"
                   + ("★陈旧" if fr.get("stale") else ""))
        print(f"    [{status}] 请求{r['requests']} 行数{obs.get('rows','-')} "
              f"列数{len(obs.get('columns',[]))}{frs}")
        if obs.get("columns"):
            print(f"      实际列: {obs['columns'][:10]}")
        if obs.get("src_date_sample"):
            print(f"      日期列 {sp.src_date_col!r} 样例: {obs['src_date_sample']!r}")
        for p in r["problems"]:
            print(f"      [问题] {p[:130]}")
        for w in r.get("warnings", [])[:2]:
            print(f"      [提示] {str(w)[:130]}")
        for s in r["suggestions"]:
            print(f"      [建议] {s[:130]}")

    dur = time.time() - t0
    ok_n = sum(1 for r in reports if r["ok"])
    print("\n" + "=" * 84)
    print("汇总")
    print("=" * 84)
    print(f"  通过 {ok_n} / 有问题 {len(reports)-ok_n}    请求数 {limiter.stats['requests']}"
          f"    耗时 {dur:.1f}s")
    print(f"  {limiter.status_str()}")

    bad = [r for r in reports if not r["ok"]]
    if bad:
        print("\n  需修正的表:")
        for r in bad:
            print(f"    {r['table']:<22} {r['problems'][0][:100] if r['problems'] else '?'}")

    if json_out:
        ensure_alt_dirs()
        # ⚠ 注意：报告结构为 {"updated":..., "tables": {表名: 报告}}，
        #   累积时必须取 old["tables"]，否则会把整个结构当成表记录嵌套进去
        #   （实测踩坑：二次运行后 old["tables"] 变成嵌套 dict，读取时 'str' 无 .get）。
        old = {}
        if os.path.exists(REPORT_FILE):
            try:
                raw = json.load(open(REPORT_FILE, encoding="utf-8"))
                old = raw.get("tables", {}) if isinstance(raw, dict) else {}
                # 清理历史嵌套脏数据
                old = {k: v for k, v in old.items() if isinstance(v, dict) and "func" in v}
            except Exception:
                old = {}
        for r in reports:
            old[r["table"]] = r
        assert_writable(REPORT_FILE)        # M1：落盘前过写入守卫
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump({"updated": datetime.now().isoformat(timespec="seconds"),
                       "tables": old}, f, ensure_ascii=False, indent=1)
        print(f"\n  报告已存: {REPORT_FILE}（累计 {len(old)} 张表）")

    return reports


def main(argv=None):
    p = argparse.ArgumentParser(description="alt 表规格校验（只读）")
    p.add_argument("--tier", choices=TIERS)
    p.add_argument("--only")
    p.add_argument("--full-enum", action="store_true",
                   help="enum 表跑全部枚举值（默认只跑首个）")
    p.add_argument("--list-todo", action="store_true", help="只列待校验表（零请求）")
    args = p.parse_args(argv)

    if args.list_todo:
        ck_path = os.path.join(os.path.dirname(ALT_META_DIR), ".alt_checkpoint.json")
        done = set()
        if os.path.exists(ck_path):
            try:
                done = set(json.load(open(ck_path, encoding="utf-8")).keys())
            except Exception:
                pass
        seps = list(BY_TIER.get(args.tier, [])) if args.tier else list(ALL_SPECS)
        todo = [s for s in seps if s.name not in done]
        print(f"待校验（尚未建库）: {len(todo)} / {len(seps)}")
        for s in todo:
            n = len(s.enum_values) if s.mode == "enum" else 1
            print(f"  {s.tier}  {s.name:<22} {s.func:<40} 约{n}次请求")
        print(f"\n  预计请求数: {sum(len(s.enum_values) if s.mode=='enum' else 1 for s in todo)}")
        return 0

    run(tier=args.tier, only=args.only, full_enum=args.full_enum)
    return 0


if __name__ == "__main__":
    sys.exit(main())
