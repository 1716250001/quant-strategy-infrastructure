# -*- coding: utf-8 -*-
"""奇点战法**引擎级全区间净值复跑**（M3 任务 6.3 / V3-3；09 §14.7 场景 B 补做）。

背景：场景 B 的信号层对账（B1/B2）与撮合层断言（B3）已交付；**引擎级净值
复跑**此前按 E3 回退披露（引擎截面与 `ctx.history` 仅 `daily` 通路）。本脚本
在 V3-1 混合表 Feed（股票 `daily` + ETF `fund_daily` + 指数 `index_daily`）与
V3-2 ETF 限价通道就位后补做，产出净值曲线级验收证据。

用法::

    python tools/run_qidian_rerun.py                     # 2019-06-26 → 固定锚 20260923
    python tools/run_qidian_rerun.py --start 20190626 --end 20260923
    python tools/run_qidian_rerun.py --end today         # 显式取今日（**须显式**）
    python tools/run_qidian_rerun.py --out <RunStore 根>  # 默认 回测产物/runs
    python tools/run_qidian_rerun.py --dry-run            # 只装配不执行

区间声明（E3）：ETF 涨跌停取 `etf_limit`（**起点 2019-06-26**）；更早区间由
BoardRule ±10% 依 pre_close 计算回退并计数披露（`fallback_limit_dates`）。
默认起点即 2019-06-26——避免落在回退区，同时满足"≥2019-06-26"验收口径。

**区间末端锚定（R52′/N56，2026-10-08）**：`--end` 缺省**不再取 `date.today()`**
（原缺省每跑一日即静默生成一个新区间档——09-23／09-30／10-08 三档并存即由此
产生）——缺省改为**固定锚 `PINNED_END=20260923`**（20 号纪律六），并在
stdout／manifest／报告「假设与披露」标注 **`period_end_source`**
（`pinned_default`／`explicit`／`today(opt-in)`）；跨区间数字**不可比**。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

KEY_METRICS = ("final_nav", "total_return", "annualized_return", "sharpe_ratio",
               "max_drawdown", "calmar_ratio", "n_fills", "total_fees",
               "turnover_annualized", "n_trading_days")

#: **R52′（N56）**：区间末端**固定锚**（缺省值；20 号纪律六三档之 09-23 档）
PINNED_END = "20260923"


def build_config(pool_symbols: list[str], start: str, end: str,
                 cash: float, weight: float) -> dict:
    """回测配置（registry 声明制：feed=mixed / 策略 module:Class）。

    **CL-2-H/R26′（19 号 §96.4）**：`analysis.metrics` 声明 15 项默认指标 ＋
    `win_rate_trades`（**可选指标**，逐笔口径辅助；默认指标集与 `metrics_digest`
    口径不变——声明制，见 metrics.OPTIONAL_METRIC_CLASSES）。
    """
    from btf.analytics.metrics import METRIC_NAMES

    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "btf.strategy.qidian:QidianStrategy",
            "params": {"weight": weight},
            "universe": {"source": "explicit", "symbols": pool_symbols},
            "period": {"start": start, "end": end},
            "initial_cash": cash,
        },
        "data": {"feed": "mixed"},
        "analysis": {"metrics": [*METRIC_NAMES, "win_rate_trades"]},
        # 与旧 43 池 run（20260927_163148_7ecdb4）生效配置对齐：空风控链
        # （显式放行——运行时强制 `allow_empty_chain=true` 并在报告披露；
        # 该 flag 为复核期新增的 fail-closed 守卫）。
        "risk": {"rules": [], "allow_empty_chain": True},
        "seed": {"master": 20260927},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="奇点战法引擎级全区间净值复跑")
    parser.add_argument("--start", default="20190626",
                        help="起点（默认 2019-06-26 = etf_limit 起点，E3）")
    parser.add_argument("--end", default=None,
                        help=f"终点（缺省 ⇒ **固定锚** {PINNED_END}；"
                             f"传 `today` 或 YYYYMMDD 显式指定——R52′/N56，"
                             f"跨区间数字不可比）")
    parser.add_argument("--cash", type=float, default=1_000_000.0)
    parser.add_argument("--weight", type=float, default=0.02,
                        help="单标的权重（43 池 × 0.02 = 0.86 ≤ 1）")
    parser.add_argument("--out", default=None, help="RunStore 根目录")
    parser.add_argument("--dry-run", action="store_true", help="只装配不执行")
    parser.add_argument("--no-report", action="store_true", help="跳过 HTML 报告")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    import time

    from btf.domain.types import TradingDate
    from btf.runtime import BTFRuntime, make_store
    from btf.strategy.qidian import load_reference, pool_of

    reference = load_reference()
    pool = pool_of(reference)
    instruments = sorted(pool)
    index_codes = sorted({entry["code"] for info in pool.values()
                          for entry in info["indices"]})
    print(f"标的池：{len(instruments)} 只 ETF/LOF + {len(index_codes)} 条配对指数"
          f"（真源 {reference.__file__}）")

    # **R52′（N56）**：末端解析与来源标注（缺省＝固定锚；today 须显式）
    if args.end is None:
        end_s, end_src = PINNED_END, "pinned_default"
    elif str(args.end).lower() == "today":
        end_s, end_src = date.today().strftime("%Y%m%d"), "today(opt-in)"
    else:
        end_s, end_src = str(args.end), "explicit"
    print(f"[区间] start={args.start} end={end_s} | "
          f"period_end_source={end_src}"
          + ("（缺省＝固定锚——如需当日请显式 `--end today`）"
             if end_src == "pinned_default" else "")
          + " | ⚠ 跨区间数字**不可比**（20 号纪律六）")

    config = build_config(instruments, f"{args.start[:4]}-{args.start[4:6]}-"
                          f"{args.start[6:]}", f"{end_s[:4]}-"
                          f"{end_s[4:6]}-{end_s[6:]}",
                          args.cash, args.weight)
    start, end = TradingDate.from_ymd(args.start), TradingDate.from_ymd(end_s)
    runtime = BTFRuntime().load_config(config).build()
    # 区间口径披露进装配说明 ⇒ **manifest ＋ 报告**（「假设与披露」）同载
    runtime.assembly_notes.append(
        f"区间口径（R52′/N56）：period_end_source={end_src}——"
        f"start={args.start}／end={end_s}；**跨区间数字不可比**，"
        f"引用必标区间（20 号纪律六）")
    print(f"装配：{len(runtime.instruments)} 标的（ETF 身份推断 + BoardRule T+N）")

    if args.dry_run:
        print("[dry-run] 装配完成（未执行引擎、未落盘）")
        return 0

    # 批量预载：逐标的 bars_of 会按年重复读（88 标的 × 8 年 ≈700 次读），
    # 预载每表仅 1 次区间读
    loaded = runtime.feed.preload(instruments + index_codes, start, end)
    print(f"预载：{loaded} 标的完成")

    store = make_store(args.out)
    t0 = time.perf_counter()
    result = runtime.run(store=store)
    elapsed = time.perf_counter() - t0

    metrics = result.metrics
    print(f"\n[ok] run_id: {result.run_id}（{elapsed:.1f}s）")
    print(f"     产物: {store.run_dir(result.run_id)}")
    for key in KEY_METRICS:
        value = metrics.get(key)
        print(f"     {key:22s} {value if value is None else round(value, 6)}")

    strategy_notes = getattr(runtime.strategy, "degraded_notes", lambda: ())()
    notes = tuple(runtime.feed.states.degraded_notes()) + tuple(strategy_notes)
    print(f"\n降级披露（{len(notes)} 条）：")
    for note in notes or ("（无）",):
        print(f"   - {note}")

    rejections: dict[str, int] = {}
    for order, rejection in result.rejections:
        rejections[rejection.code.value] = rejections.get(rejection.code.value, 0) + 1
    print(f"拒单：{sum(rejections.values())} 单 {rejections or '{}'}")

    if not args.no_report:
        from dataclasses import replace

        from btf.app.services import assemble_assumptions
        from btf.viz.report import ReportBuilder

        bundle = store.load(result.run_id)
        # RC-03（2026-10-05）：假设字段统一走 `assemble_assumptions`
        # （读 `manifest.config_effective`）——不再硬编码滑点/撮合，
        # 仅在本工具特有项（degraded / notes）上做覆盖。
        assumptions = replace(
            assemble_assumptions(store, bundle), degraded=notes,
            notes=(f"奇点战法引擎级复跑（V3-1 混合表 Feed）"
                   f"{args.start}–{args.end}；"
                   f"初始资金 {args.cash:,.0f} 元 / 单标权重 {args.weight}",
                   "ETF 涨跌停取 etf_limit（起点 2019-06-26，E3）；"
                   "更早区间由 BoardRule ±10% 计算回退并披露"),
        )
        html = ReportBuilder(bundle, assumptions).build()
        out = store.run_dir(result.run_id) / "report.html"
        out.write_text(html, encoding="utf-8")
        print(f"\n报告: {out}（{out.stat().st_size:,} 字节，含假设与披露章节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
