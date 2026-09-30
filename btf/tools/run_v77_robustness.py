# -*- coding: utf-8 -*-
"""v7.7 多区间 × 多基准 × 多参数**稳健性验证**（业务议题；19 号 §23.2 / §38.7.3）。

**为什么需要**：截至 v0.5.13，v7.7 的可采信结论只有 **1 区间 × 1 基准 × 1 参数**
（2023-01~2026-09 / 000300.SH / top_k=10，IR 0.0548），不足以支撑"策略有效"的
判断（§23.2 曾据此立"业务议题"，§38 改判为**体系恒演进项**、移出 btf 1.0 门槛）。
本工具把该议题**做成可复跑的证据矩阵**：不同市场环境的分段区间 × 两个基准 ×
两档参数，逐格落盘（`persist=True`，真入口链路：RunStore + metrics.json），
再算**相对基准**的超额与 IR（btf 核心指标集不含基准项，故在此显式计算）。

**纪律**：
    · 只读主库 + 规则镜像；产物写 `--out`（默认 `paths.OUTPUT_DIR/v77-robustness/`，
      即 量化策略/回测产物/v77-robustness/，与引擎 runs/ 同根）；
    · 逐格独立 `btf` run（不共享进程内状态），失败即如实登记（不吞异常）；
    · 结论必须带 `metrics.json` 口径字段 + 相对基准算法说明（可复核）。

用法：
    python tools/run_v77_robustness.py                 # 默认矩阵（5 区间 × 2 参数）
    python tools/run_v77_robustness.py --params 10     # 只跑 top_k=10
    python tools/run_v77_robustness.py --periods 0,4   # 指定区间索引
退出码：0=全部完成 / 1=有失败格
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:                     # 脚本自举（btf 包）
    sys.path.insert(0, str(ROOT))

from btf.config.paths import OUTPUT_DIR  # 产物路径单一真源（paths.py）

OUT_DEFAULT = OUTPUT_DIR / "v77-robustness"       # = 工作区根/回测产物/v77-robustness
MIRROR = Path(r"D:\量化策略\赤潮\rules_mirror_v77.json")

#: 分段区间（覆盖不同市场环境：熔断后修复 / 结构牛 / 熊市与波动 / 当前）
PERIODS: list[tuple[str, str, str]] = [
    ("P1 修复期", "2016-01-04", "2018-12-28"),
    ("P2 结构牛", "2019-01-02", "2021-12-31"),
    ("P3 熊市与震荡", "2022-01-04", "2024-12-31"),
    ("P4 当前", "2025-01-02", "2026-09-23"),
    ("ALL 全区间", "2016-01-04", "2026-09-23"),
]
#: 参数档（top_k）
K_VALUES = (10, 5)
#: **EE-1 对照区间**（原 §23.2 唯一可采信结论的区间）——`--ee1` 直接复跑该单格，
#: 与赤潮双路径参照值逐位比对（P1-NEW-9 的可复现入口；19 号 §55.1.3）
EE1 = {"label": "EE-1 Z-6 对照（原 §23.2 区间）",
       "start": "2023-01-01", "end": "2026-09-23", "top_k": 10}
#: 参照值（赤潮 §54.3.1 双路径；容差 1e-4）
EE1_REF = {"000300.SH|return": 0.161882, "000300.SH|excess": 0.071938,
           "000300.SH|ir": 0.050426, "total_return": 0.233820,
           "n_fills": 441, "n_days": 904}
#: 基准（须在 index_daily 内可读）
BENCHMARKS = ("000300.SH", "000905.SH")


def _config(start: str, end: str, top_k: int) -> dict:
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": "btf.strategy.v77:V77Strategy",
            "params": {"top_k": top_k},
            "universe": {"source": "all"},
            "period": {"start": start, "end": end},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": "tushare_parquet"},
        "execution": {"handler": "next_open", "cost_model": "flat_rate",
                      "rebalancer": "full"},
        "risk": {"rules": [{"name": "tradability"}, {"name": "max_weight"},
                           {"name": "cash_check"}]},
        "rules": {"source": "mirror_json", "path": str(MIRROR)},
        "report": {"benchmark": BENCHMARKS[0]},
    }


def _ymd(value: str) -> str:
    """日期入参**规范化**为 `YYYYMMDD` 紧凑式（去连字符；容忍已是紧凑式）。

    P1-NEW-9（19 号 §54.3 / §55）：`_bench_series` 曾用 `start <= str(day) <= end`
    做**字典序**比较，而 `day` 来自 parquet（紧凑 `"20260923"`）、`start/end` 为
    `"2026-09-23"`（连字符）—— 第 5 字符 `'0'(0x30) > '-'(0x2D)` 使
    **end 所在年份（2026）的全部行被过滤** ⇒ 基准终点被静默截到 `2025-12-31`
    （正是赤潮复现出的 `+19.09%` 生成路径）。**所有日期比较必须先经本函数**。
    """
    return value.replace("-", "").replace("/", "")


def _bench_series(symbol: str, start: str, end: str) -> dict[str, float]:
    """基准收盘序列（经 `data.core` 唯一 IO 出口；缺失年跳过）。

    `start`/`end` 可传 `YYYY-MM-DD` 或 `YYYYMMDD`；**比较一律用紧凑式**（`_ymd`）。
    """
    from btf.config.paths import MARKET_DATA_DIR
    from btf.data.core import YearTableStore

    start_c, end_c = _ymd(start), _ymd(end)
    store = YearTableStore(Path(MARKET_DATA_DIR))
    out: dict[str, float] = {}
    for year in range(int(start_c[:4]), int(end_c[:4]) + 1):
        t = store.read_file("index_daily", year=year,
                            columns=["ts_code", "trade_date", "close"])
        if t is None:
            continue
        for code, day, close in zip(t.column("ts_code").to_pylist(),
                                    t.column("trade_date").to_pylist(),
                                    t.column("close").to_pylist(), strict=True):
            if code == symbol and start_c <= _ymd(str(day)) <= end_c and close:
                out[_ymd(str(day))] = float(close)
    return dict(sorted(out.items()))


def _excess_ir(nav: list[tuple[str, float]], bench: dict[str, float]
               ) -> dict[str, float | None]:
    """相对基准：区间收益差 / 日频 IR（年化）/ 基准区间收益 + **端点留痕**。

    端点（`strat_first/last`、`bench_first/last`）一并返回并落盘（`run_cell`）：
    P1-NEW-9 的教训是"**两侧端点不一致**会被静默算成一个数"，故端点必须可核。
    """
    if not nav or not bench:
        return {"excess_return": None, "information_ratio": None,
                "benchmark_return": None, "strat_first": None,
                "strat_last": None, "bench_first": None, "bench_last": None}
    days = [d for d, _ in nav]
    bench_days = sorted(d for d in bench if days[0] <= d <= days[-1])
    if len(bench_days) < 2:
        return {"excess_return": None, "information_ratio": None,
                "benchmark_return": None, "strat_first": days[0],
                "strat_last": days[-1], "bench_first": None, "bench_last": None}
    b0, b1 = bench[bench_days[0]], bench[bench_days[-1]]
    bench_ret = b1 / b0 - 1.0
    strat_ret = nav[-1][1] / nav[0][1] - 1.0
    # 日频超额：以策略净值序列为基准对齐交易日
    closes = {d: v for d, v in nav}
    diff: list[float] = []
    prev_s = prev_b = None
    for i, d in enumerate(bench_days):
        s = closes.get(d)
        if s is None:
            continue
        b = bench[d]
        if prev_s is not None and prev_b:
            diff.append((s / prev_s - 1.0) - (b / prev_b - 1.0))
        prev_s, prev_b = s, b
    ir = None
    if len(diff) > 2:
        mean = sum(diff) / len(diff)
        var = sum((x - mean) ** 2 for x in diff) / (len(diff) - 1)
        sd = math.sqrt(var)
        ir = (mean / sd * math.sqrt(252)) if sd > 0 else None
    return {"excess_return": strat_ret - bench_ret,
            "information_ratio": ir,
            "benchmark_return": bench_ret,
            "strat_first": days[0], "strat_last": days[-1],
            "bench_first": bench_days[0], "bench_last": bench_days[-1]}


def run_cell(label: str, start: str, end: str, top_k: int,
             out_dir: Path) -> dict:
    """单格：真入口（`persist=True`）跑一次 → metrics.json + 相对基准。"""
    from btf.runtime import BTFRuntime, make_store

    t0 = time.perf_counter()
    rt = BTFRuntime().load_config(_config(start, end, top_k)).build()
    result = rt.run(store=make_store(out_dir / "runs"))
    wall = time.perf_counter() - t0
    nav = [(s.date.to_ymd(), s.total_value) for s in result.snapshots]
    row: dict = {"cell": f"{label} | top_k={top_k}", "start": start,
                 "end": end, "top_k": top_k, "wall_s": round(wall, 1),
                 "n_days": result.n_days, "n_fills": len(result.fills),
                 "run_id": result.run_id}
    row.update({k: result.metrics.get(k) for k in (
        "total_return", "annualized_return", "sharpe_ratio", "max_drawdown",
        "calmar_ratio", "total_turnover", "fee_ratio", "n_fills")})
    for bench in BENCHMARKS:
        series = _bench_series(bench, start, end)
        extra = _excess_ir(nav, series)
        row[f"{bench}|return"] = extra["benchmark_return"]
        row[f"{bench}|excess"] = extra["excess_return"]
        row[f"{bench}|ir"] = extra["information_ratio"]
        # 端点留痕（P1-NEW-9 教训：两侧端点不一致必须一眼可见，不能只落一个比值）
        row[f"{bench}|strat_first"] = extra["strat_first"]
        row[f"{bench}|strat_last"] = extra["strat_last"]
        row[f"{bench}|bench_first"] = extra["bench_first"]
        row[f"{bench}|bench_last"] = extra["bench_last"]
        row[f"{bench}|aligned"] = bool(
            extra["strat_first"] == extra["bench_first"]
            and extra["strat_last"] == extra["bench_last"])
    return row


def _fmt(value: object, pct: bool = False, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value * 100:.2f}%" if pct else f"{value:.{digits}f}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v7.7 多区间稳健性验证")
    parser.add_argument("--out", default=str(OUT_DEFAULT))
    parser.add_argument("--params", default="", help="逗号分隔 top_k（默认 10,5）")
    parser.add_argument("--periods", default="", help="逗号分隔区间索引（默认全部）")
    parser.add_argument("--ee1", action="store_true",
                        help="只复跑 EE-1 对照区间并与双路径参照值逐位比对")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not MIRROR.is_file():
        print(f"[error] 规则镜像缺失：{MIRROR}", file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    if args.ee1:
        # EE-1 复现入口（P1-NEW-9）：单格 + 与双路径参照值逐位比对 + 端点核对
        out_dir.mkdir(parents=True, exist_ok=True)
        row = run_cell(EE1["label"], EE1["start"], EE1["end"], EE1["top_k"],
                       out_dir)
        print(f"[ee1] run_id={row['run_id']} | 端点 "
              f"策略 {row['000300.SH|strat_first']}→{row['000300.SH|strat_last']}"
              f" / 基准 {row['000300.SH|bench_first']}→"
              f"{row['000300.SH|bench_last']} | aligned="
              f"{row['000300.SH|aligned']}")
        for key, ref in EE1_REF.items():
            got = row.get(key)
            ok = got is not None and abs(float(got) - float(ref)) <= 1e-4
            print(f"  {key:24s} 本机 {got!r:24s} 参照 {ref!r:14s} "
                  f"{'✅' if ok else '❌'}")
            if not ok:
                return 1
        print("[ee1] ✅ 与双路径参照值逐位一致（容差 1e-4）")
        return 0
    ks = [int(x) for x in args.params.split(",") if x.strip()] or list(K_VALUES)
    idx = ([int(x) for x in args.periods.split(",") if x.strip()]
           or list(range(len(PERIODS))))

    rows: list[dict] = []
    fails: list[str] = []
    for i in idx:
        label, start, end = PERIODS[i]
        for top_k in ks:
            try:
                row = run_cell(label, start, end, top_k, out_dir)
            except Exception as exc:                    # 如实登记，不吞
                fails.append(f"{label} | top_k={top_k}: {type(exc).__name__}: {exc}")
                print(f"[fail] {label} | top_k={top_k} → {exc}")
                continue
            rows.append(row)
            print(f"[ok] {row['cell']}: 交易日 {row['n_days']} | "
                  f"收益 {_fmt(row['total_return'], pct=True)} | "
                  f"夏普 {_fmt(row['sharpe_ratio'])} | 回撤 "
                  f"{_fmt(row['max_drawdown'], pct=True)} | "
                  f"vs 000300 超额 {_fmt(row['000300.SH|excess'], pct=True)} | "
                  f"IR {_fmt(row['000300.SH|ir'])} | {row['wall_s']}s")
            if not row.get("000300.SH|aligned", True):
                print(f"     ⚠ 端点不一致（P1-NEW-9 族）："
                      f"策略 {row['000300.SH|strat_first']}→"
                      f"{row['000300.SH|strat_last']} vs 基准 "
                      f"{row['000300.SH|bench_first']}→"
                      f"{row['000300.SH|bench_last']}")

    misaligned = [r["cell"] for r in rows
                  if not all(r.get(f"{b}|aligned", True) for b in BENCHMARKS)]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    payload = {"generated_at": stamp, "engine": "btf",
               "periods": [p[0] for p in PERIODS], "k_values": ks,
               "benchmarks": list(BENCHMARKS), "rows": rows, "fails": fails,
               "endpoint_misaligned": misaligned}
    (out_dir / f"v77-robustness-{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[summary] {len(rows)} 格完成 / {len(fails)} 格失败 → {out_dir}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
