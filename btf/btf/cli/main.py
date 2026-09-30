# -*- coding: utf-8 -*-
"""bt 命令行入口（M1 任务 4.3 config-check；M2 任务 5.9 六命令族）。

注册表式命令表（COMMANDS：名字 → (处理器, 帮助, 参数注册器)）——新增命令
= 表加一行，不修改 main() 分发骨架（04 §8.5 声明制在 CLI 层的同构落地）。

命令族（18 号 5.9）：
    config-check   四层合并 + Schema 校验 + 脱敏摘要
    run            执行回测并落盘（--dry-run：只装配不执行）
    report         由 RunStore 产物再生成 HTML 报告（幂等）
    verify         R1 复核：落盘产物重算指标 vs manifest.metrics_digest
    test           运行测试层（pytest 执行器；--layer/--pattern）
    dataset        黄金集构建（build-golden 子命令，委托 tools/build_golden.py）

--dry-run 语义（明确定义，避免歧义）：load_config + build 全部执行（校验
配置与插件装配），**不创建 run 目录、不运行引擎、不落盘**，仅打印装配计划。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

CommandHandler = Callable[[argparse.Namespace], int]
ArgAdder = Callable[[argparse.ArgumentParser], None]


def _filtered_env() -> dict[str, str]:
    """`BTF_*` 环境过滤（**单源在 `btf.app`**，P2-3；原 cli/api 各一份）。"""
    from btf.app import filter_btf_env

    return filter_btf_env()


def _utf8() -> None:
    """中文/全角输出纪律（06 §11.3）。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# ① config-check
# ─────────────────────────────────────────────────────────────
def _fmt_metric(value: Any) -> str:
    """指标展示（BB-2，19 号 §33.6②）：与 `metrics.json` **落盘一致**。

    落盘对不可算指标写 `null`（严格 JSON 合法），而终端原样 `print(nan)` ——
    Python `nan` 记法易被误读为"落盘了非标准 NaN"，且 `nan` 非 JSON 概念。
    统一为 `null`（含 None / NaN 两态）。
    """
    if value is None:
        return "null"
    if isinstance(value, float) and value != value:      # NaN
        return "null"
    return str(value)


def _cmd_config_check(args: argparse.Namespace) -> int:
    """bt config-check <config.yaml>：四层合并 + Schema 校验 + 脱敏摘要。"""
    from btf.config.loader import ConfigSourceError, load_config
    from btf.config.mask import mask_config
    from btf.config.validation import ConfigError, validate

    try:
        cfg = load_config(Path(args.config), environ=dict(_filtered_env()))
        validate(cfg)
    except ConfigSourceError as e:
        print(f"[error] 配置源读取失败：{e}", file=sys.stderr)
        return 1
    except ConfigError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    run = cfg.get("run", {})
    period = run.get("period", {})
    data = cfg.get("data", {})
    execn = cfg.get("execution", {})
    rules = cfg.get("rules")
    print(f"[ok] {cfg['schema_version']} 校验通过：{args.config}")
    print(f"     策略: {run.get('strategy', '—')}")
    print(f"     期间: {period.get('start', '?')} → {period.get('end', '?')}"
          f"（初始资金 {run.get('initial_cash', '—')}）")
    # X-6（19 号 §18.2）：原打印 `data.completeness.missing_bars_action`
    # ——该键已于 v0.5.1 作为死键删除（声明而零消费），此处仍消费即为残留；
    # 改为打印**真实生效**的区间守卫降级开关（P1-11）。
    print(f"     数据: feed={data.get('feed')}"
          f" | 降级开关 allow_degraded_limit="
          f"{data.get('allow_degraded_limit', False)}")
    print(f"     执行: handler={execn.get('handler')} cost={execn.get('cost_model')}"
          f" slippage={execn.get('slippage', {}).get('model')}"
          f" rebalancer={execn.get('rebalancer')}")
    if isinstance(rules, dict):
        print(f"     规则: source={rules.get('source')}"
              f" overrides={len(rules.get('overrides') or [])} 条")
    if getattr(args, "resolve_strategy", False):
        # BB-3（19 号 §33.6③ / P3-NEW-5）：策略类可加载性——「随写随测」的首道
        # 门；类名拼错应在**最低成本环节**暴露（原须到 `run` 才报错）。
        # 默认关闭：`config-check` 仍只做「四层合并 + Schema」，行为向后兼容。
        problem = _resolve_strategy_problem(run.get("strategy"))
        if problem:
            print(f"[error] 策略不可加载：{problem}", file=sys.stderr)
            return 1
        print(f"     策略可加载: {run.get('strategy')} ✓")
    if args.dump:
        import yaml

        print("--- 合并后配置（脱敏） ---")
        print(yaml.safe_dump(mask_config(cfg), allow_unicode=True, sort_keys=False))
    return 0


def _resolve_strategy_problem(spec: Any) -> str | None:
    """检查 `module:Class` 可加载（BB-3）：返回问题描述或 None。

    只做**最低成本**校验：模块可 import + 类属性存在 + 是 StrategyBase 子类
    （构造参数/契约协商仍归 `build()`）。
    """
    if not spec or not isinstance(spec, str) or ":" not in spec:
        return f"run.strategy 需形如 module:Class，得 {spec!r}"
    module_name, _, class_name = spec.partition(":")
    import importlib

    try:
        module = importlib.import_module(module_name)
    except Exception as exc:                     # 模块侧任意加载错误均归此处
        return (f"模块 import 失败 {module_name!r}（{type(exc).__name__}: {exc}）"
                f"——若为项目外策略，请确认其在 sys.path（PYTHONPATH）上")
    cls = getattr(module, class_name, None)
    if cls is None:
        return (f"模块 {module_name!r} 无属性 {class_name!r}"
                f"（module … has no attribute {class_name!r}）")
    from btf.strategy.base import StrategyBase

    if not (isinstance(cls, type) and issubclass(cls, StrategyBase)):
        return f"{spec!r} 不是 StrategyBase 子类"
    return None


def _args_config_check(p: argparse.ArgumentParser) -> None:
    p.add_argument("config", help="用户配置 YAML 路径")
    p.add_argument("--dump", action="store_true",
                   help="输出合并后全量配置（敏感项脱敏）")
    p.add_argument("--resolve-strategy", action="store_true",
                   help="额外尝试加载 run.strategy 指向的类（BB-3：拼写错在"
                        "本环节即暴露；默认关闭，行为向后兼容）")


# ─────────────────────────────────────────────────────────────
# ② run
# ─────────────────────────────────────────────────────────────
def _parse_overrides(pairs: list[str] | None) -> dict[str, Any]:
    """`--set a__b=1` → 嵌套覆盖（**单源在 `btf.app`**，P2-3 第 2 组重复）。"""
    from btf.app import parse_overrides

    return parse_overrides(pairs)


def _cmd_run(args: argparse.Namespace) -> int:
    """bt run --config <yaml> [--dry-run] [--out DIR]：执行回测并落盘。

    编排**委托 `btf.app.run_backtest`**（P2-3 第 3 组重复收敛）：CLI 与
    `api.run` 走同一次装配/执行（api.py 的"字节等价"承诺由同源实现保证）。
    """
    from btf.app import run_backtest
    from btf.runtime import SHORT_PERIOD_TRADING_DAYS, make_store

    try:
        overrides = _parse_overrides(args.set)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    try:
        outcome = run_backtest(Path(args.config), overrides=overrides,
                               out=args.out, dry_run=args.dry_run)
    except Exception as exc:                       # 装配期失败：显式报错退出
        print(f"[error] 装配失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    rt = outcome.runtime

    if args.dry_run:
        run_cfg = rt.config["run"]
        print("[dry-run] 装配完成（未执行引擎、未落盘）")
        print(f"     策略: {type(rt.strategy).__name__}"
              f" | 标的: {len(rt.instruments)} 只")
        print(f"     期间: {run_cfg['period']['start']} → {run_cfg['period']['end']}")
        print(f"     组件: handler={type(rt.handler).__name__}"
              f" cost={type(rt.cost_model).__name__}"
              f" slip={type(rt.slippage).__name__}"
              f" rebalancer={type(rt.rebalancer).__name__}")
        print(f"     风控: {len(rt.risk_manager.rules) if rt.risk_manager else 0} 条"
              f" | 规则版本: {rt._rules_version()}")
        return 0

    store = make_store(args.out)
    result = outcome.result
    print(f"[ok] run_id: {result.run_id}")
    print(f"     产物: {store.run_dir(result.run_id)}")
    for key in ("total_return", "annualized_return", "sharpe_ratio",
                "max_drawdown", "n_fills"):
        print(f"     {key}: {_fmt_metric(result.metrics.get(key))}")
    # R5-4（19 号附录 D.3）：`--verbose` 分步耗时 + 装配期披露摘要。
    # 为什么需要：长回测（十年全市场）此前只有"跑完/没跑完"两种观测，无法
    # 判断时间花在装配（数据/质检/组件）还是引擎日循环——`optimize` 调参
    # 与性能回归定位都需要这一行。
    if getattr(args, "verbose", False):
        print(f"     [verbose] 分步耗时: {rt.step_timings_line()}")
        if rt.data_quality:
            print(f"     [verbose] 数据质检: "
                  f"{rt.data_quality.get('note') or rt.data_quality.get('ok')}"
                  f"（标的 {rt.data_quality.get('checked_symbols')}，"
                  f"抽样={rt.data_quality.get('sampled_symbols')}）")
        if rt.data_version_info:
            print(f"     [verbose] 数据指纹: "
                  f"{rt.data_version_info.get('content_hash')}"
                  f"（档位 {rt.data_version_info.get('mode')}，"
                  f"水位 {rt.data_version_info.get('anchor_date')}）")
    # 极短区间告警（BB-4 / OBS-1，19 号 §33.6④）：**不阻断**，但必须提示
    # ——极短区间的部分指标"数学有定义、业务无意义"（如 3 日区间
    # information_ratio = −31.83），会污染 optimize --objective 排序。
    if result.n_days < SHORT_PERIOD_TRADING_DAYS:
        print(f"[warn] 区间仅 {result.n_days} 个交易日（< "
              f"{SHORT_PERIOD_TRADING_DAYS}）：部分指标数学有定义但业务无意义，"
              f"勿用于 optimize --objective 排序（19 号 OBS-1/BB-4）")
    return 0


def _args_run(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", required=True, help="回测配置 YAML")
    p.add_argument("--set", action="append", metavar="K=V",
                   help="CLI 覆盖（L4 层；a__b=1 嵌套）")
    p.add_argument("--dry-run", action="store_true",
                   help="只装配不执行（不建 run 目录、不落盘）")
    p.add_argument("--out", help="产物根目录（默认 BTF_OUTPUT_DIR/runs）")
    p.add_argument("--verbose", action="store_true",
                   help="输出分步耗时 / 数据质检 / 数据指纹（R5-4）")


# ─────────────────────────────────────────────────────────────
# ③ report
# ─────────────────────────────────────────────────────────────
def _cmd_report(args: argparse.Namespace) -> int:
    """bt report --run <run_id>：由产物再生成 HTML 报告（幂等）。

    报告装配**委托 `btf.app.build_report_file`**（P2-3 第 4 组重复收敛）。
    """
    from btf.app import build_report_file

    try:
        out = build_report_file(args.run, out=args.out, out_dir=args.out_dir)
    except FileNotFoundError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(f"[ok] 报告: {out}（{out.stat().st_size:,} 字节）")
    return 0


def _args_report(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", required=True, help="run_id")
    p.add_argument("--out", help="输出 HTML 路径（默认 run 目录/report.html）")
    p.add_argument("--out-dir", help="RunStore 根目录（默认产物目录）")


# ─────────────────────────────────────────────────────────────
# ④ verify（R1 复核）
# ─────────────────────────────────────────────────────────────
def _cmd_verify(args: argparse.Namespace) -> int:
    """bt verify --run <run_id>：落盘产物重算指标 vs manifest.metrics_digest。"""
    from btf.runtime import BTFRuntime, make_store

    store = make_store(args.out_dir)
    try:
        report = BTFRuntime().verify_run(args.run, store=store)
    except FileNotFoundError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(f"run_id      : {report['run_id']}（{report['status']}）")
    print(f"快照 / 成交 : {report['n_snapshots']} / {report['n_fills']}")
    print(f"重算 digest : {report['digest']}"
          f"（{'一致' if report['recompute_match'] else '不一致'}）")
    print(f"落盘 digest : {report['stored_digest']}"
          f"（{'一致' if report['stored_match'] else '不一致'}）")
    print(f"manifest    : {report['expected']}")
    if report["match"]:
        print("[ok] R1 复核通过：重算指标与落盘指标均与 manifest 摘要一致")
        return 0
    print("[fail] R1 复核失败：摘要不一致（产物被篡改或口径漂移）",
          file=sys.stderr)
    return 1


def _args_verify(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", required=True, help="run_id")
    p.add_argument("--out-dir", help="RunStore 根目录（默认产物目录）")


# ─────────────────────────────────────────────────────────────
# ⑤ test
# ─────────────────────────────────────────────────────────────
def _cmd_test(args: argparse.Namespace) -> int:
    """bt test [--layer l1] [--pattern EXPR]：运行测试层（pytest 执行器）。

    子进程隔离（X-11，19 号 §17.4.1 / §18.2 / §20.4.1）：本命令以**子进程**
    跑 pytest，而全量回归中它可能被父 pytest 调用（`test_runs_smoke_subset`）
    ——父子共用同一仓库根与临时根，两类干扰已实证：
      ① `.pytest_cache` 争用（父子并发写 `lastfailed`/`nodeids`）；
      ② **pytest 临时目录 GC**：默认 `--basetemp` 模式下 pytest 会在会话起
         始批量删除历史 `pytest-of-<user>/pytest-N` 目录（实测一次 329 个
         文件），在带"批量删除守卫"的环境里被拦截 → 子进程退出码非 0 →
         用例误判为"测试失败"（第三轮审查环境归因，非 btf 缺陷）。
    故子进程显式 `-p no:cacheprovider` + `--basetemp`（**显式 basetemp 下
    pytest 不做历史临时目录 GC**）+ 剥离 `PYTEST_CURRENT_TEST`。
    """
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    basetemp = Path(tempfile.mkdtemp(prefix="btf_bt_test_"))
    cmd += ["--basetemp", str(basetemp)]
    if args.layer and args.layer != "all":
        cmd += ["-m", args.layer]
    if args.pattern:
        cmd += ["-k", args.pattern]
    if args.path:
        cmd.append(args.path)
    print(f"[run] {' '.join(cmd[2:])}")
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    try:
        return subprocess.call(cmd, cwd=str(Path(__file__).resolve().parents[2]),
                               env=env)
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)   # 自建目录自行回收


def _args_test(p: argparse.ArgumentParser) -> None:
    p.add_argument("--layer", default="l1",
                   help="测试层标记：l1|l2|l3|l4|l5|all")
    p.add_argument("--pattern", help="pytest -k 表达式")
    p.add_argument("--path", help="测试路径（默认全量）")


# ─────────────────────────────────────────────────────────────
# ⑥ dataset
# ─────────────────────────────────────────────────────────────
def _cmd_dataset(args: argparse.Namespace) -> int:
    """bt dataset build-golden：黄金集构建（委托 tools/build_golden.py）。

    子进程编排**委托 `btf.app.build_dataset`**（P2-3 第 5 组重复收敛）。
    """
    from btf.app import build_dataset

    return build_dataset(args.cases, action=args.action, verify=args.verify,
                         force=args.force, list_only=args.list, out=args.out)


def _args_dataset(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", nargs="?", default="build-golden",
                   help="子动作：build-golden")
    p.add_argument("--cases", default="g1", help="案例（g1|all|逗号分隔）")
    p.add_argument("--list", action="store_true", help="列出案例与待补组")
    p.add_argument("--verify", action="store_true", help="只校验不写")
    p.add_argument("--force", action="store_true", help="显式覆盖（留痕）")
    p.add_argument("--out", help="输出目录")


# ─────────────────────────────────────────────────────────────
# ⑦ optimize（v0.5 V5-3：grid_search 进程池）
# ─────────────────────────────────────────────────────────────
def _parse_grid(spec: str) -> dict[str, list[Any]]:
    """--grid "run.params.top_n=5,10;run.params.rebalance_day=1,2" → 网格。

    键为点分配置路径；值按 int → float → str 顺序推断类型（与 --set 同则）。
    """
    from btf.app import parse_override_value

    grid: dict[str, list[Any]] = {}
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, _, values = chunk.partition("=")
        if not key or not values:
            raise ValueError(f"--grid 需形如 path=v1,v2，得 {chunk!r}")
        # 类型推断**单源**（btf.app；原 --set / --grid / grid.deep_set 三份）
        grid[key.strip()] = [parse_override_value(raw)
                             for raw in values.split(",")]
    if not grid:
        raise ValueError("--grid 为空（至少一个参数位）")
    return grid


def _cmd_optimize(args: argparse.Namespace) -> int:
    """bt optimize --config <yaml> --grid <spec>：网格搜索并打印排名。"""
    import json

    from btf import registry
    from btf.config.loader import load_config

    try:
        grid = _parse_grid(args.grid)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    try:
        config = load_config(Path(args.config), environ=dict(_filtered_env()))
    except Exception as exc:
        print(f"[error] 配置读取失败：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    # 经 registry 名字表取优化器（铁律 5：cli 不直连 optimize——传递依赖
    # runtime→engine 会触发 import-linter 拦截；名字表即声明制消费方式）
    optimizer = registry.create(registry.OPTIMIZER, "grid_search",
                                {"workers": args.workers})
    report = optimizer.run(config, grid, objective=args.objective)

    print(f"[ok] grid_search：{report['n_combos']} 组 × {report['workers']} 进程"
          f" | objective={report['objective']}")
    print(f"{'rank':>4}  {'value':>10}  params")
    for item in report["top"][: args.top]:
        params = ", ".join(f"{k}={v}" for k, v in sorted(item["params"].items()))
        print(f"{item['rank']:>4}  {item['value']:>10.4f}  {params}")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                  default=str), encoding="utf-8")
        print(f"     结果: {out}")
    return 0


def _args_optimize(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", required=True, help="回测配置 YAML")
    p.add_argument("--grid", required=True,
                   help='网格 spec："path=v1,v2;path2=v1,v2"（点分配置路径）')
    p.add_argument("--objective", default="sharpe_ratio",
                   help="排名指标（默认 sharpe_ratio）")
    p.add_argument("--workers", type=int, default=8,
                   help="进程数（默认 8；0=内联顺序执行——调试/注入型 feed）")
    p.add_argument("--top", type=int, default=10, help="打印前 N 名（默认 10）")
    p.add_argument("--out", help="结果 JSON 输出路径（可选）")


#: 命令注册表：名字 → (处理器, 帮助, 参数注册器)
COMMANDS: dict[str, tuple[CommandHandler, str, ArgAdder]] = {
    "config-check": (_cmd_config_check,
                     "校验回测配置：四层合并 + JSON Schema（bt config-check <config.yaml>）",
                     _args_config_check),
    "run": (_cmd_run, "执行回测并落盘（--dry-run 只装配不执行）", _args_run),
    "report": (_cmd_report, "由 RunStore 产物再生成 HTML 报告（幂等）", _args_report),
    "verify": (_cmd_verify, "R1 复核：产物重算指标 vs manifest.metrics_digest",
               _args_verify),
    "test": (_cmd_test, "运行测试层（pytest 执行器）", _args_test),
    "dataset": (_cmd_dataset, "黄金数据集构建（build-golden）", _args_dataset),
    "optimize": (_cmd_optimize,
                 "参数网格搜索（grid_search 进程池；v0.5 V5-3）", _args_optimize),
}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    parser = argparse.ArgumentParser(
        prog="bt", description="btf 回测工具（七命令族：config-check/run/"
                               "report/verify/test/dataset/optimize）")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_handler, help_text, add_args) in sorted(COMMANDS.items()):
        add_args(sub.add_parser(name, help=help_text))

    args = parser.parse_args(argv)
    handler: Any = COMMANDS[args.command][0]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
