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

用法：
    python tools/run_l2_ablation.py                      # 全矩阵（6 臂 × 6 区间）
    python tools/run_l2_ablation.py --arms 0,1,2         # 指定臂
    python tools/run_l2_ablation.py --periods 4          # 只跑 P4 当前
    python tools/run_l2_ablation.py --quick              # 单区间快速冒烟
退出码：0=全部完成 / 1=有失败格
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

STRATEGY = "btf.strategy.l2_ablation:L2AblationStrategy"

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


def _config(start: str, end: str, selection: str, ranking: str,
            top_k: int, seed: int) -> dict:
    return {
        "schema_version": "backtest.v1",
        "run": {
            "strategy": STRATEGY,
            "params": {"selection": selection, "ranking": ranking,
                       "top_k": top_k, "seed": seed},
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

    if args.ee1:
        # ── 一致性锚 ──
        # A1（selection=current + ranking=code）在算法上应当**逐位等价于**
        # V77Strategy（screen_l2 的 sorted(picks) + [:top_k]）。
        # 用 rob.EE1_REF（赤潮 §54.3.1 双路径参照值）逐位比对：
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
        for key, ref in rob.EE1_REF.items():
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
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
