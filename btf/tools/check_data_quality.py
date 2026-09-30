# -*- coding: utf-8 -*-
"""数据不变量校验机检（批 8 CC-6；19 号 §39.2「CC-6 工程化」常跑项）。

三件事（**含负向对照**——铁律新 15：防线必须可被证伪）：
    ① **正样本**：主库固定切片（20 标的 × 1 年）跑 CC-1..CC-5 → 必须 `ok`；
       （选用固定切片而非全市场：全市场单年实测含 22 行**退市整理期**残余，
        不适合做"必须全绿"的门；全市场口径见 `--full` 只报不进退出码。）
    ② **负向对照**：在**临时目录**复制切片并注入 5 类缺陷（OHLC 矛盾／涨跌幅
       尖刺／停牌日有行情／因子断层／有价无量）→ 每条**必须被检出**；
    ③ **不得恒空**（铁律新 16）：`checked_rows` 逐规则 > 0。

用法: python tools/check_data_quality.py     （= `bt check` 第 8/9 项；单跑仅用于定位）
退出码：0 = 全绿；1 = 正样本失败或任一注入缺陷漏检。
用法：python tools/check_data_quality.py [--full]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from btf.config.paths import MARKET_DATA_DIR
from btf.data.quality import run_checks

SYMS = ["000001.SZ", "000002.SZ", "000063.SZ", "000333.SZ", "000651.SZ",
        "000858.SZ", "002415.SZ", "600009.SH", "600028.SH", "600030.SH",
        "600036.SH", "600276.SH", "600519.SH", "600887.SH", "600900.SH",
        "601166.SH", "601288.SH", "601318.SH", "601398.SH", "601988.SH"]
START, END = "20240101", "20241231"


def _copy_slice(dst: Path) -> None:
    """复制主库固定切片（日线一年 + 相关表）到临时目录（供注入用）。"""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    master = Path(MARKET_DATA_DIR)
    for table in ("daily", "stk_limit", "adj_factor", "suspend_d"):
        (dst / table).mkdir(parents=True, exist_ok=True)
        src = master / table / "2024.parquet"
        if src.is_file():
            t = pq.read_table(src)
            mask = pc.is_in(t.column("ts_code"), value_set=pa.array(SYMS))
            pq.write_table(t.filter(mask), dst / table / "2024.parquet")
    (dst / "metadata").mkdir(parents=True, exist_ok=True)
    for name in ("trade_cal", "stock_basic"):
        src = master / "metadata" / f"{name}.parquet"
        if src.is_file():
            shutil.copy2(src, dst / "metadata" / f"{name}.parquet")
    (dst / "dividend").mkdir(parents=True, exist_ok=True)
    dv = master / "dividend" / "2024.parquet"
    if dv.is_file():
        shutil.copy2(dv, dst / "dividend" / "2024.parquet")


def _st_control() -> list[str]:
    """⑥ CC-2 **ST 分支**变异对照（批 9 / DD-2；铁律新 15）。

    用「**+8%**」这一**介于板块 10% 与 ST 5% 之间**的信号做双向断言：
      · 传 `st_symbols` → **必须检出**（分支真在跑）；
      · 不传 → **必须不检出**（证明检出**只可能**来自 ST 分支，分支有区分度）。
    任一项不成立 ⇒ 该分支为伪防线（或失效），必须变红。
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    warnings: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _copy_slice(tmp)
        p = tmp / "daily" / "2024.parquet"
        t = pq.read_table(p)
        dates = t.column("trade_date").to_pylist()
        codes = t.column("ts_code").to_pylist()
        sym = SYMS[0]
        own = [d for c, d in zip(codes, dates, strict=True) if c == sym]
        if len(own) < 40:
            return ["CC-2 ST 对照注入失败（切片行数不足）"]
        day = own[len(own) // 2]                 # 中段常态日（远离上市/复牌豁免）
        idx = next(i for i, (c, d) in enumerate(zip(codes, dates, strict=True))
                   if c == sym and d == day)
        pre = t.column("pre_close").to_pylist()
        closes = t.column("close").to_pylist()
        pre[idx] = (closes[idx] or 1.0) / 1.08   # +8%
        pq.write_table(
            t.set_column(t.schema.get_field_index("pre_close"), "pre_close",
                         pa.array(pre, type=t.schema.field("pre_close").type)),
            p)
        with_st = run_checks(tmp, START, END, symbols=set(SYMS),
                             st_symbols={(sym, day)})
        if with_st.rules[1].ok:                  # CC-2
            warnings.append("CC-2 ST 分支未检出（伪防线）")
        bare = run_checks(tmp, START, END, symbols=set(SYMS))
        if not bare.rules[1].ok:
            warnings.append(
                "CC-2 ST 对照无区分度（不传 ST 也报 ⇒ 检出并非来自 ST 分支）")
    return warnings


def _delisting_control() -> list[str]:
    """⑦ CC-2 **退市整理期**双向对照（EE-2 / OBS-3；铁律新 15）。

    在临时切片上把 `SYMS[1]` 的 `delist_date` 设为 **2024-12-31**（制造 15 交易日
    整理期窗口），然后：
      · 在**窗口首日**注入 −80% 假尖刺 → **必须不报**（首日不设涨跌幅）；
      · 在**窗口中段日**注入 −60% 假尖刺 → **必须报**（板块上限 10%）。
    两项任一不成立 ⇒ 首日豁免写反 / 窗口定位错 ⇒ 机检变红。
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    warnings: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _copy_slice(tmp)
        sym = SYMS[1]
        # ① 造窗口：delist_date = 2024-12-31
        bp = tmp / "metadata" / "stock_basic.parquet"
        b = pq.read_table(bp)
        codes = b.column("ts_code").to_pylist()
        if sym not in codes:
            return [f"整理期对照：切片缺 {sym}（stock_basic）"]
        idx = codes.index(sym)
        dd = b.column("delist_date").to_pylist()
        dd[idx] = "20241231"
        b = b.set_column(b.schema.get_field_index("delist_date"), "delist_date",
                         pa.array(dd, type=b.schema.field("delist_date").type))
        pq.write_table(b, bp)

        # ② 窗口交易日（2024 年最后 15 个交易日）→ 取首日 + 中段日
        p = tmp / "daily" / "2024.parquet"
        t = pq.read_table(p)
        days = sorted({d for c, d in zip(t.column("ts_code").to_pylist(),
                                         t.column("trade_date").to_pylist(),
                                         strict=True) if c == sym})
        if len(days) < 20:
            return [f"整理期对照：{sym} 交易日不足（{len(days)}）"]
        # 窗口 = 摘牌日（`days[-1]` = 20241231，不入窗口）**之前** 15 个交易日
        window = days[-16:-1]
        first_day, mid_day = window[0], window[len(window) // 2]

        base = pq.read_table(p)
        closes = base.column("close").to_pylist()
        pre = base.column("pre_close").to_pylist()
        codes2 = base.column("ts_code").to_pylist()
        dates2 = base.column("trade_date").to_pylist()

        def inject(day: str, factor: float) -> None:
            i = next(k for k, (c, d) in enumerate(zip(codes2, dates2,
                                                      strict=True))
                     if c == sym and d == day)
            pre[i] = (closes[i] or 1.0) * factor      # −80% / −60% 尖刺
            pq.write_table(
                base.set_column(base.schema.get_field_index("pre_close"),
                                "pre_close",
                                pa.array(pre, type=base.schema.field(
                                    "pre_close").type)), p)

        inject(first_day, 5.0)
        rep = run_checks(tmp, START, END, symbols=set(SYMS), st_symbols=set())
        if not rep.rules[1].ok:
            warnings.append("整理期**首日**豁免失效（首日不设涨跌幅却被检出）")

        inject(first_day, 1.0)                        # 还原首日
        inject(mid_day, 2.5)
        rep = run_checks(tmp, START, END, symbols=set(SYMS), st_symbols=set())
        if rep.rules[1].ok:
            warnings.append("整理期**非首日**尖刺未检出（伪防线）")
    return warnings


def _inject_and_detect(tmp: Path) -> list[str]:
    """负向对照：注入 5 类缺陷，逐条验证「必须被检出」。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    _copy_slice(tmp)
    warnings: list[str] = []

    def mutate(table: str, fn) -> None:
        p = tmp / table / "2024.parquet"
        t = pq.read_table(p)
        pq.write_table(fn(t), p)

    # ① CC-1：把第一行 high 改小（high < max(open, close)）
    def bad_ohlc(t):
        hi = t.column("high").to_pylist()
        hi[0] = 0.0
        return t.set_column(t.schema.get_field_index("high"), "high",
                            pa.array(hi, type=t.schema.field("high").type))

    mutate("daily", bad_ohlc)
    rep = run_checks(tmp, START, END, symbols=set(SYMS), st_symbols=set())
    if rep.rules[0].ok:                       # CC-1
        warnings.append("CC-1 注入缺陷未检出（伪防线）")

    # ② CC-2：选中段常态日（20240603，远离上市/复牌豁免窗）把 pre_close
    #    改成 close/5（制造 −80% 的假"暴跌"）
    def bad_pct(t):
        days = t.column("trade_date").to_pylist()
        closes = t.column("close").to_pylist()
        pre = t.column("pre_close").to_pylist()
        hit = [i for i, d in enumerate(days) if d == "20240603"]
        if not hit:
            warnings.append("CC-2 注入失败（日线缺 20240603）")
            return t
        i = hit[0]
        pre[i] = (closes[i] or 1.0) / 5.0
        return t.set_column(t.schema.get_field_index("pre_close"), "pre_close",
                            pa.array(pre, type=t.schema.field("pre_close").type))

    mutate("daily", bad_pct)
    rep = run_checks(tmp, START, END, symbols=set(SYMS), st_symbols=set())
    if rep.rules[1].ok:                       # CC-2
        warnings.append("CC-2 注入缺陷未检出（伪防线）")

    # ③ CC-3：把某个全日停牌日**加回**一行行情（状态与行情错配）
    susp = pq.read_table(tmp / "suspend_d" / "2024.parquet",
                         columns=["ts_code", "trade_date", "suspend_type",
                                  "suspend_timing"])
    pair = None
    for code, day, typ, timing in zip(
            susp.column("ts_code").to_pylist(),
            susp.column("trade_date").to_pylist(),
            susp.column("suspend_type").to_pylist(),
            susp.column("suspend_timing").to_pylist(), strict=True):
        if typ == "S" and not timing:
            pair = (code, day)
            break
    if pair is not None:
        def add_suspended_row(t):
            row = {name: t.column(name)[0].as_py() for name in t.column_names}
            row["ts_code"], row["trade_date"] = pair
            return pa.concat_tables([t, pa.table(
                {k: [v] for k, v in row.items()},
                schema=t.schema)])

        mutate("daily", add_suspended_row)
        rep = run_checks(tmp, START, END, symbols=set(SYMS), st_symbols=set())
        if rep.rules[2].ok:                   # CC-3
            warnings.append("CC-3 注入缺陷未检出（伪防线）")

    # ④ CC-4：把某行 adj_factor 改成 0（非正）
    def bad_factor(t):
        vals = t.column("adj_factor").to_pylist()
        vals[0] = 0.0
        return t.set_column(t.schema.get_field_index("adj_factor"),
                            "adj_factor",
                            pa.array(vals,
                                     type=t.schema.field("adj_factor").type))

    mutate("adj_factor", bad_factor)
    rep = run_checks(tmp, START, END, symbols=set(SYMS), st_symbols=set())
    if rep.rules[3].ok:                       # CC-4
        warnings.append("CC-4 注入缺陷未检出（伪防线）")

    # ⑤ CC-5：把某行 vol 清零（有价无量）
    def zero_vol(t):
        vals = t.column("vol").to_pylist()
        vals[1] = 0.0
        return t.set_column(t.schema.get_field_index("vol"), "vol",
                            pa.array(vals, type=t.schema.field("vol").type))

    mutate("daily", zero_vol)
    rep = run_checks(tmp, START, END, symbols=set(SYMS), st_symbols=set())
    if rep.rules[4].ok:                       # CC-5
        warnings.append("CC-5 注入缺陷未检出（伪防线）")

    for r in rep.rules:                       # ③ 不得恒空
        if r.checked_rows == 0:
            warnings.append(f"{r.rule} checked_rows=0（空洞披露，铁律新 16）")
    return warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="另跑全市场一年（只报不进退出码；含退市整理期残余）")
    args = ap.parse_args()

    print("=== 正样本：主库 20 标的 × 1 年 ===")
    rep = run_checks(MARKET_DATA_DIR, START, END, symbols=set(SYMS),
                     st_symbols=set())
    print(rep.summary_line())
    ok = rep.ok
    for r in rep.rules:
        if not r.ok:
            for f in r.findings:
                print(f"  {r.rule} {f.table}: {f.detail} → {f.n_rows} 行 "
                      f"样例 {f.samples[:2]}")
    if any(r.checked_rows == 0 for r in rep.rules):
        print("  [FAIL] checked_rows 为 0（空洞披露）")
        ok = False

    print("\n=== 负向对照：注入 5 类缺陷 → 必须全部检出 ===")
    with tempfile.TemporaryDirectory() as td:
        warnings = _inject_and_detect(Path(td))
    if warnings:
        for w in warnings:
            print(f"  [FAIL] {w}")
        ok = False
    else:
        print("  5/5 缺陷均被检出（防线可被证伪 ✓）")

    print("\n=== 负向对照 ⑥：CC-2 **ST 分支**（+8% 双向）→ 传 ST 必检 / 不传必不检 ===")
    st_warnings = _st_control()
    if st_warnings:
        for w in st_warnings:
            print(f"  [FAIL] {w}")
        ok = False
    else:
        print("  ST 分支有区分度且真在跑（铁律新 15 ✓）")

    print("\n=== 负向对照 ⑦：CC-2 **退市整理期**双向（EE-2）→ 首日必不报 / 非首日必报 ===")
    de_warnings = _delisting_control()
    if de_warnings:
        for w in de_warnings:
            print(f"  [FAIL] {w}")
        ok = False
    else:
        print("  整理期首日豁免 + 非首日检出双向成立（铁律新 15 ✓）")

    if args.full:
        print("\n=== 参考：全市场一年（不进退出码）===")
        full = run_checks(MARKET_DATA_DIR, START, END, st_symbols=set())
        print(" ", full.summary_line())
        for r in full.rules:
            if not r.ok:
                for f in r.findings:
                    print(f"  {r.rule} {f.table}: {f.detail} → {f.n_rows} 行 "
                          f"样例 {f.samples[:2]}")

    print(f"\n--- 数据不变量校验机检: {'PASS' if ok else 'FAIL'} ---")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
