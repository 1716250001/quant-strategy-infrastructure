# -*- coding: utf-8 -*-
"""L2 选股**消融矩阵**（老大 2026-10-01 令 · 决策项 1）。

把 2026-08-21 v7.4 审计用一次性脚本得出的「L2 选股是 alpha」结论，
在 **btf 稳定引擎**上重算一遍：`selection × ranking × 期间` 逐格真入口跑，
同一 engine / 同一数据 IO / 同一指标体系，只让选股方式成为变量。

**对照组（`ARMS`）**：详见 `btf/strategy/l2_ablation.py` 模块 docstring。
核心归因路径：
    · `none+random`      → 无选股基准（BASE）
    · `current+code`     → **btf v77 现状实际行为**（FF-3：代码字母序）
    · `current+pe_asc`   → v77 docstring 声称的"低估优先"
    · `multifactor+score`→ 08-21 报告推荐并验证的方案 E
    · `pr+score`         → 08-21 报告方案 C（PR<1.5）
    · `none+pe_asc`      → 纯排序效应（不经四条硬筛）

**纪律**：
    · 只读主库 + 规则镜像；产物写 `--out`（默认 `paths.OUTPUT_DIR/l2-ablation/`）；
    · 逐格独立 run（不共享进程内状态），失败如实登记（不吞异常）；
    · **复用** `tools/run_v77_robustness` 的 `_bench_series` / `_excess_ir`
      （不准自己再写一份 IR —— 那是"同一指标两个口径"的经典漂移源）。

**A5/A6 负控制组固化（HH-2，2026-10-14；19 号 §128）**：
    A5（none+pe_asc）/ A6（none+code）是**负控制臂**——用于分离"筛选净效应 vs
    排序净效应"。本工具把二者固化为**例行对照入口**：
      · **每次运行**（全矩阵）结束自动打印「A5/A6 负控制对照块」——
        实测 vs **2026-10-01 冻结基线**（`A56_BASELINE`，容差 `A56_TOL`）；
      · 专用巡查入口 `--a56`（只跑 A5/A6，默认 ALL 区间）——每轮复算巡查
        **基线漂移**用（L2 复算报告 §9 待办第 3 条的落地）。
    ⚠ 基线是**口径绑定**的（preRC 零摩擦 / top_k=10 / seed=42 / 2016-01-04
    ~2026-09-23）；任何口径变更后须**重冻结基线并留痕**，不得"重跑即更新"。

用法：
    python tools/run_l2_ablation.py                      # 全矩阵（6 臂 × 6 区间）
    python tools/run_l2_ablation.py --arms 0,1,2         # 指定臂
    python tools/run_l2_ablation.py --periods 4          # 只跑 P4 当前
    python tools/run_l2_ablation.py --a56                # A5/A6 例行对照（默认 ALL）
    python tools/run_l2_ablation.py --quick              # 单区间快速冒烟
退出码：0=全部完成（--a56 时含对照全一致）/ 1=有失败格或对照漂移
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:                     # 脚本自举（btf 包）
    sys.path.insert(0, str(ROOT))

from btf.config.paths import OUTPUT_DIR

OUT_DEFAULT = OUTPUT_DIR / "l2-ablation"
MIRROR = Path(r"D:\量化策略\赤潮\rules_mirror_v77.json")

#: **唯一的事务管线**：基准序列与 IR 算法一律取自 run_v77_robustness
sys.path.insert(0, str(ROOT / "tools"))
import run_v77_robustness as rob

PERIODS = rob.PERIODS
BENCHMARKS = rob.BENCHMARKS

STRATEGY = "cc_strategies.l2_ablation:L2AblationStrategy"

#: 消融臂（selection, ranking, 说明）
ARMS: list[tuple[str, str, str, str]] = [
    ("A0", "none", "random", "无选股基准（随机）"),
    ("A1", "current", "code", "现行四筛 + 代码序【v77 现状实际行为 FF-3】"),
    ("A2", "current", "pe_asc", "现行四筛 + PE升序【docstring 声称】"),
    ("A3", "multifactor", "score", "方案E 多因子打分【08-21 报告推荐】"),
    ("A4", "pr", "score", "方案C PR<1.5【08-21 收益最高】"),
    ("A5", "none", "pe_asc", "纯 PE 升序（不经筛）→ 分离排序效应"),
    # A6 为正交表补的关键一格：与 A1 同用 code 排序、但不经过滤 ⇒
    # A1 − A6 = **纯过滤效应**（在同一种荒谬排序下的净贡献）。
    # 同理 A2 − A5 = 纯过滤效应（在 PE 升序下的净贡献）。
    ("A6", "none", "code", "无过滤 + 代码序【隔离四条硬筛净效应】"),
]

#: **A5/A6 负控制对照基线（2026-10-01 冻结；HH-2 固化，19 号 §128）**
#: 来源 = `回测产物/l2-ablation/l2-ablation-20261001-{1118,1122}.json` 精确值
#: （preRC 零摩擦、top_k=10、seed=42、超额 vs 000300.SH、全精度）。
#: 用途 = 每轮复算巡查"筛选/排序净效应"基线漂移（L2 复算报告 §9 待办第 3 条）。
A56_BASELINE: dict[tuple[str, str], dict[str, float]] = {
    ("A5", "P1 修复期"): {"excess": 0.16043978806212045,
                          "ir": 0.29278361884573867, "n_fills": 339},
    ("A5", "P2 结构牛"): {"excess": -0.7908328615242146,
                          "ir": -1.46991758124953, "n_fills": 388},
    ("A5", "P3 熊市与震荡"): {"excess": 0.3076322597063639,
                              "ir": 0.6759754767100936, "n_fills": 381},
    ("A5", "P4 当前"): {"excess": -0.2142435027830849,
                        "ir": -0.8340173421425537, "n_fills": 212},
    ("A5", "ALL 全区间"): {"excess": -0.35168482732363815,
                           "ir": -0.2797957113419044, "n_fills": 1312},
    ("A6", "P1 修复期"): {"excess": -0.0590979119378785,
                          "ir": -0.23159940393554354, "n_fills": 297},
    ("A6", "P2 结构牛"): {"excess": -0.3040164836642141,
                          "ir": -0.552068862831601, "n_fills": 329},
    ("A6", "P3 熊市与震荡"): {"excess": 0.1319760007963635,
                              "ir": 0.32757916997729497, "n_fills": 328},
    ("A6", "P4 当前"): {"excess": -0.09713874179308446,
                        "ir": -0.4949840038833622, "n_fills": 183},
    ("A6", "ALL 全区间"): {"excess": -0.1907344282136365,
                           "ir": -0.1876607800916784, "n_fills": 1141},
}
#: 对照容差（沿用 EE-1 锚惯例）
A56_TOL = 1e-4


def _config(start: str, end: str, selection: str, ranking: str,
            top_k: int, seed: int) -> dict:
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": STRATEGY,
            "strategy_paths": ["D:/量化策略/赤潮/研究"],
            "params": {"selection": selection, "ranking": ranking,
                       "top_k": top_k, "seed": seed},
            "universe": {"source": "all"},
            "period": {"start": start, "end": end},
            "initial_cash": 1_000_000,
        },
        "data": {"feed": "tushare_parquet"},
        # ⚠ **成本口径显式声明（HH-2，2026-10-14；GG-1 纪律推广 / P1-NEW-12 防复发）**：
        #   本工具矩阵与 A5/A6 冻结基线（2026-10-01）**均为 preRC 零摩擦口径**。
        #   RC-02（2026-10-05）把**全局默认**改为 0.1% 滑点 + 5% 参与率后，
        #   隐式继承默认会让本工具静默切到"交付档"——实测（HH-2 首跑）：
        #   A5 = −37.64% / −0.2961，与冻结基线"假漂移"Δ−2.47pp；
        #   **该数字精确等于 §62.3 三档表的交付默认列** ⇒ 口径漂移、非数据漂移。
        #   ⇒ 与 run_v77_robustness `COST_PROFILES` 同法：**绝不依赖全局默认**；
        #   现实语义档（delivered/stress）见 L2 复算报告 §4.3 三档表（独立探针口径）。
        "execution": {"handler": "next_open", "cost_model": "flat_rate",
                      "rebalancer": "full",
                      "slippage": {"model": "none"},
                      "handler_params": {"volume_participation": None}},
        "risk": {"rules": [{"name": "tradability"}, {"name": "max_weight"},
                           {"name": "cash_check"}]},
        "rules": {"source": "mirror_json", "path": str(MIRROR)},
        "report": {"benchmark": BENCHMARKS[0]},
    }


def run_cell(arm_id: str, selection: str, ranking: str, note: str,
             label: str, start: str, end: str, top_k: int, seed: int,
             out_dir: Path) -> dict:
    """单格：真入口跑一次 → metrics + 相对基准（复用 rob 的口径）。

    seed **必须透传**（不可在函数内写死）：`ranking="random"` 的选股结果由
    seed 决定，写死会让多种子稳健性检验静默退化成同一条曲线的重复
    （2026-10-01 修：原实现硬编码 42，`--seed` 形同虚设）。
    """
    from btf.runtime import BTFRuntime, make_store

    t0 = time.perf_counter()
    rt = (BTFRuntime()
          .load_config(_config(start, end, selection, ranking, top_k, seed))
          .build())
    result = rt.run(store=make_store(out_dir / "runs"))
    wall = time.perf_counter() - t0
    nav = [(s.date.to_ymd(), s.total_value) for s in result.snapshots]
    row: dict = {"arm": arm_id, "selection": selection, "ranking": ranking,
                 "note": note, "period": label, "start": start, "end": end,
                 "top_k": top_k, "wall_s": round(wall, 1),
                 "n_days": result.n_days, "run_id": result.run_id}
    row.update({k: result.metrics.get(k) for k in (
        "total_return", "annualized_return", "sharpe_ratio", "max_drawdown",
        "calmar_ratio", "total_turnover", "fee_ratio", "n_fills")})
    for bench in BENCHMARKS:
        series = rob._bench_series(bench, start, end)
        extra = rob._excess_ir(nav, series)
        row[f"{bench}|return"] = extra["benchmark_return"]
        row[f"{bench}|excess"] = extra["excess_return"]
        row[f"{bench}|ir"] = extra["information_ratio"]
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


def report_a56(rows: list[dict]) -> bool:
    """A5/A6 负控制对照块（HH-2）：实测 vs 2026-10-01 冻结基线 → 全一致？

    只呈现 A5/A6 的**超额 / IR / 成交数**三项（V1 口径绑定点）；容差 `A56_TOL`。
    无对照行（未跑该臂/该区间）⇒ 明确显示「未跑」，不冒充通过。
    """
    got = {(r["arm"], r["period"]): r for r in rows
           if r.get("arm") in ("A5", "A6") and r.get("top_k") == 10}
    print("\n── A5/A6 负控制对照块（HH-2；基线＝2026-10-01 冻结，容差 "
          f"{A56_TOL:g}）──")
    print(f"  {'臂':4s} {'区间':12s} {'超额(本机)':>10s} {'超额(基线)':>11s} "
          f"{'Δpp':>7s} {'IR(本机)':>9s} {'IR(基线)':>9s} {'成交':>5s} 判定")
    ok_all = True
    for (arm, period), base in sorted(A56_BASELINE.items()):
        r = got.get((arm, period))
        if r is None:
            print(f"  {arm:4s} {period:12s} {'未跑':>10s} "
                  f"{base['excess'] * 100:>10.2f}% {'—':>7s}——")
            continue
        ex, ir = float(r.get("000300.SH|excess")), float(r.get("000300.SH|ir"))
        d_ex, d_ir = ex - base["excess"], ir - base["ir"]
        ok = abs(d_ex) <= A56_TOL and abs(d_ir) <= A56_TOL
        ok_all = ok_all and ok
        print(f"  {arm:4s} {period:12s} {ex * 100:>9.2f}% "
              f"{base['excess'] * 100:>10.2f}% {d_ex * 100:>+6.2f}pp "
              f"{ir:>9.4f} {base['ir']:>9.4f} "
              f"{int(r.get('n_fills') or 0):>5d} {'✓一致' if ok else '✗漂移'}")
    print(f"  → {'全部一致 ✓' if ok_all else '存在漂移 ✗（口径变更须重冻结基线并留痕）'}")
    return ok_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L2 选股消融矩阵")
    parser.add_argument("--out", default=str(OUT_DEFAULT))
    parser.add_argument("--arms", default="", help="逗号分隔臂索引（默认全部）")
    parser.add_argument("--periods", default="", help="逗号分隔区间索引（默认全部）")
    parser.add_argument("--top-k", default="10", help="逗号分隔 top_k（默认 10）")
    parser.add_argument("--seed", default="42", help="随机臂种子（默认 42）")
    parser.add_argument("--ee1", action="store_true",
                        help="一致性锚：复跑 EE-1 区间，验证 A1(current+code) "
                             "与 V77Strategy 现状逐位等价（见下方说明）")
    parser.add_argument("--a56", action="store_true", dest="a56",
                        help="A5/A6 负控制例行对照入口（HH-2；只跑 A5/A6，"
                             "默认 ALL 区间，输出与 2026-10-01 基线对照块）")
    parser.add_argument("--quick", action="store_true",
                        help="单臂单区间冒烟（验证链路，不产出结论）")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not MIRROR.is_file():
        print(f"[error] 规则镜像缺失：{MIRROR}", file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.quick:
        arm = ARMS[1]
        row = run_cell(arm[0], arm[1], arm[2], arm[3], "QA 冒烟",
                       "2023-01-01", "2023-12-29", 10, 42, out_dir)
        print(f"[quick] {json.dumps(row, ensure_ascii=False, indent=2)}")
        return 0

    if args.a56:
        # ── A5/A6 负控制例行对照入口（HH-2，19 号 §128）──
        #   默认只跑 ALL 全区间（巡查最快）；`--periods` 可扩到分段区间。
        idx = ([int(x) for x in args.periods.split(",") if x.strip()]
               or [len(PERIODS) - 1])
        rows = []
        fails: list[str] = []
        for i in idx:
            label, start, end = PERIODS[i]
            for ai in (5, 6):                      # ARMS 索引：A5 / A6 固定
                arm_id, selection, ranking, note = ARMS[ai]
                try:
                    row = run_cell(arm_id, selection, ranking, note, label,
                                   start, end, 10, args.seed, out_dir)
                except Exception as exc:           # 如实登记，不吞
                    fails.append(f"{arm_id} {label}: "
                                 f"{type(exc).__name__}: {exc}")
                    print(f"[fail] {arm_id} | {label} → {exc}")
                    continue
                rows.append(row)
                print(f"[a56] {arm_id} {selection}+{ranking} | {label} | "
                      f"超额 {_fmt(row['000300.SH|excess'], pct=True)} | "
                      f"IR {_fmt(row['000300.SH|ir'])} | {row['wall_s']}s")
        ok = report_a56(rows) and not fails
        return 0 if ok else 1

    if args.ee1:
        # ── 一致性锚 ──
        # A1（selection=current + ranking=code）在算法上应当**逐位等价于**
        # V77Strategy（screen_l2 的 sorted(picks) + [:top_k]）。
        # 用 rob.EE1_REFS["preRC"]（赤潮 §54.3.1 双路径参照值；GG-1b 后为**双锚
        # dict**，本矩阵 preRC 零摩擦口径取 preRC 锚——与下方 HH-2 注记同口径）
        # 逐位比对：
        # 通过 ⇒ 其余臂与 A1 的差异**纯粹**来自 selection/ranking 变量；
        # 不通过 ⇒ 本策略与现状不等价，整份消融结论作废。
        ee = rob.EE1
        arm = ARMS[1]                      # A1 = current + code
        row = run_cell(arm[0], arm[1], arm[2], arm[3], ee["label"],
                       ee["start"], ee["end"], ee["top_k"], 42, out_dir)
        print(f"[ee1] run_id={row['run_id']} | 端点 策略 "
              f"{row['000300.SH|strat_first']}→{row['000300.SH|strat_last']}"
              f" / 基准 {row['000300.SH|bench_first']}→"
              f"{row['000300.SH|bench_last']} | aligned="
              f"{row['000300.SH|aligned']}")
        ok_all = True
        # ⚠ HH-2 连带修复（2026-10-14）：原引用 `rob.EE1_REF` 为 **GG-1b 重构前的
        #   旧名**（现为 `EE1_REFS` 双锚 dict）⇒ `--ee1` 自 GG-1b 起必抛
        #   AttributeError（P1-NEW-12 同族：对账入口随上游重构失效）。
        #   本工具矩阵为 **preRC 零摩擦**口径 ⇒ 取 `EE1_REFS["preRC"]` 单锚；
        #   delivered 锚对照见 `run_v77_robustness --ee1`（双锚）与 L2 报告 §4.3。
        for key, ref in rob.EE1_REFS["preRC"].items():
            got = row.get(key)
            ok = got is not None and abs(float(got) - float(ref)) <= 1e-4
            ok_all = ok_all and ok
            print(f"  {key:24s} 本机 {got!r:24s} 参照 {ref!r:14s} "
                  f"{'✅' if ok else '❌'}")
        print("[ee1] " + ("✅ A1 ≡ v77 现状逐位一致（容差 1e-4）—— 锚成立，"
                          "其余臂差异可归因于 selection/ranking"
                          if ok_all else
                          "❌ A1 与 v77 现状不一致 —— 锚不成立，"
                          "消融结论须作废重查"))
        return 0 if ok_all else 1

    arm_idx = ([int(x) for x in args.arms.split(",") if x.strip()]
               or list(range(len(ARMS))))
    per_idx = ([int(x) for x in args.periods.split(",") if x.strip()]
               or list(range(len(PERIODS))))
    ks = [int(x) for x in args.top_k.split(",") if x.strip()] or [10]

    rows: list[dict] = []
    fails: list[str] = []
    total = len(arm_idx) * len(per_idx) * len(ks)
    done = 0
    for i in per_idx:
        label, start, end = PERIODS[i]
        for ai in arm_idx:
            arm_id, selection, ranking, note = ARMS[ai]
            for top_k in ks:
                done += 1
                try:
                    row = run_cell(arm_id, selection, ranking, note, label,
                                   start, end, top_k, args.seed, out_dir)
                except Exception as exc:            # 如实登记，不吞
                    fails.append(f"{arm_id} {label} k={top_k}: "
                                 f"{type(exc).__name__}: {exc}")
                    print(f"[fail] {arm_id} | {label} | k={top_k} → {exc}")
                    continue
                rows.append(row)
                print(f"[{done}/{total}] {arm_id} {selection}+{ranking} | {label}"
                      f" | 收益 {_fmt(row['total_return'], pct=True)}"
                      f" | 夏普 {_fmt(row['sharpe_ratio'])}"
                      f" | 回撤 {_fmt(row['max_drawdown'], pct=True)}"
                      f" | vs300 超额 {_fmt(row['000300.SH|excess'], pct=True)}"
                      f" | IR {_fmt(row['000300.SH|ir'])}"
                      f" | {row['wall_s']}s")
                if not row.get("000300.SH|aligned", True):
                    print(f"     ⚠ 端点不一致：策略 "
                          f"{row['000300.SH|strat_first']}→"
                          f"{row['000300.SH|strat_last']} vs 基准 "
                          f"{row['000300.SH|bench_first']}→"
                          f"{row['000300.SH|bench_last']}")

    misaligned = [f"{r['arm']}|{r['period']}" for r in rows
                  if not all(r.get(f"{b}|aligned", True) for b in BENCHMARKS)]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    payload = {
        "generated_at": stamp, "engine": "btf", "tool": "run_l2_ablation",
        "purpose": "08-21 v7.4 审计 L2 选股结论在 btf 上的复算（2026-10-01 令）",
        "selection": [{"arm": a[0], "selection": a[1], "ranking": a[2],
                       "note": a[3]} for a in ARMS],
        "periods": [p[0] for p in PERIODS],
        "benchmarks": list(BENCHMARKS), "top_k": ks,
        "rows": rows, "fails": fails, "endpoint_misaligned": misaligned,
    }
    json_path = out_dir / f"l2-ablation-{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"\n[summary] {len(rows)}/{total} 格完成 / {len(fails)} 格失败")
    print(f"[out] {json_path}")
    # HH-2：**每次运行**输出 A5/A6 负控制对照块（矩阵含 A5/A6 时；未含则显示"未跑"）
    report_a56(rows)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
