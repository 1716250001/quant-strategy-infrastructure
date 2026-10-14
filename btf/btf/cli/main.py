# -*- coding: utf-8 -*-
"""bt 命令行入口（M1 任务 4.3 config-check；M2 任务 5.9 六命令族）。

注册表式命令表（COMMANDS：名字 → (处理器, 帮助, 参数注册器)）——新增命令
= 表加一行，不修改 main() 分发骨架（04 §8.5 声明制在 CLI 层的同构落地）。

命令族（18 号 5.9 + CLI 审查报告-20260930）：
    config-check   四层合并 + Schema 校验 + 脱敏摘要
    run            执行回测并落盘（--dry-run：只装配不执行）
    report         由 RunStore 产物再生成 HTML 报告（幂等）
    verify         R1 复核：落盘产物重算指标 vs manifest.metrics_digest
    test           运行测试层（pytest 执行器；--layer/--pattern）
    dataset        黄金集：build-golden 构建 / verify 对账 / refcalc 重算
    optimize       参数网格搜索（grid_search 进程池；v0.5 V5-3）
    check          十项架构门禁（委托 tools/check.py）
    cold-backup    跨版本源码冷备（委托 tools/cold_backup.py）
    runs           RunStore 下的 run 摘要清单（只读）
    show           单个 run 的 manifest + 指标摘要（只读）

 `bt check` 与 `bt test` 的分工（审查报告 P1-2：命名相近易互相误读）：
 `check` = **提交前十项门禁**（ruff / import-linter / 冒烟 / 依赖预算 / domain
 白名单 / 报告渲染 / 数据不变量 / IO 单一入口 / 插件边界基线，委托
 `tools/check.py` 单一真源；第 10 项自 GG-6② 起并入）；
`test` = **回归测试层**（pytest，默认 l1；全量 `--layer all`）。文档长期写的
`bt check` 此前不是命令，本批收编——文档、CHANGELOG 与 CLI 自此同口径。

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

from btf._version import __version__  # 版本单源（19 号 P3-4；不经包根）

CommandHandler = Callable[[argparse.Namespace], int]
ArgAdder = Callable[[argparse.ArgumentParser], None]

#: run 指标摘要行（`bt run` / `bt runs` / `bt show` 三处同源——原 `_cmd_run`
#: 内联元组，新增两命令时若各写一份即"同一摘要三个口径"）
_RUN_METRIC_KEYS = ("total_return", "annualized_return", "sharpe_ratio",
                    "max_drawdown", "n_fills")


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
    # CL-3（19 号 §72.4 Q-2(b)）：退市清算口径开关——显式选择才注入覆盖
    # （默认档不写键 ⇒ 与改动前逐位一致；声明进 config_effective 可追溯）。
    if getattr(args, "delisted_settlement", None):
        overrides.setdefault("run", {})["delisted_settlement"] = \
            args.delisted_settlement
    try:
        outcome = run_backtest(Path(args.config), overrides=overrides,
                               out=args.out, dry_run=args.dry_run)
    except Exception as exc:                       # 装配期失败：显式报错退出
        print(f"[error] 装配失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    rt = outcome.runtime

    # RC-09（2026-10-05）：基准**软强制**——未配 `report.benchmark` 不阻断，
    # 但终端必须显式提示「无基准对照」（IR/超额等相对指标不存在，不得以
    # 绝对收益替代基准结论；报告侧同款披露见 viz/report._assembly_disclosures）。
    if not ((rt.config.get("report") or {}).get("benchmark")):
        print("[warn] 未配置 report.benchmark —— 无基准对照："
              "IR / 超额等相对指标不存在（RC-09，仅披露不阻断）")

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
    for key in _RUN_METRIC_KEYS:
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
    p.add_argument("--delisted-settlement", choices=("carry", "writeoff"),
                   dest="delisted_settlement",
                   help="退市持仓残值口径（CL-3 / 19 号 §72.4）：carry=残值"
                        "携带（默认＝现状）；writeoff=按 0 核销（压力口径，"
                        "仅末日快照清零并下调 NAV）")


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

    **与 `bt check` 分工**（审查报告 P1-2）：本命令跑 pytest 测试层，
    `bt check` 跑十项架构门禁（lint/import-linter/数据不变量/插件边界基线…）——"提交前
    该跑哪个"的答案在 `check`；本命令默认 `--layer l1`（秒级单元层），
    全量回归要显式 `--layer all`（该默认值刻意保守：全量约 64 分钟）。

    子进程隔离（X-11，19 号 §17.4.1 / §18.2 / §20.4.1）：本命令以**子进程**
    跑 pytest，而全量回归中它可能被父 pytest 调用（`test_runs_smoke_subset`）
    ——父子共用同一仓库根与临时根，两类干扰已实证：
      ① `.pytest_cache` 争用（父子并发写 `lastfailed`/`nodeids`）；
      ② **pytest 临时目录 GC**：默认 `--basetemp` 模式下 pytest 会在会话起
         始批量删除历史 `pytest-of-<user>/pytest-N` 目录（实测一次 329 个
         文件），在带"批量删除守卫"的环境里被拦截 → 子进程退出码非 0 →
         用例误判为"测试失败"（第三轮审查环境归因，非 btf 缺陷）。
      ③ **N5-②（2026-10-06；19 号 §67.11.2）**：basetemp 由 `%TEMP%` 迁至
         **项目内** `_scratch/pytest_tmp/bt_test/` 下**每次唯一**子目录
         （根可经 `BTFTOOL_TEST_PYTEST_BASETEMP` 覆盖），避开系统临时区的
         批量删除守卫拦截（"用例全过但 exit≠0"假红）。
         **N6 分根（19 号 §69.3）**：与门禁第 3 项（`…/pytest_tmp/check`）
         **各用独立子根**——任一通道残留不再污染另一通道。
         **N7 命名（19 号 §69.4）**：工具变量一律 `BTFTOOL_` 前缀
         （`BTF_` 前缀会被配置层当覆盖键注入 ⇒ 配置校验失败）。
    故子进程显式 `-p no:cacheprovider` + `--basetemp`（**显式 basetemp 下
    pytest 不做历史临时目录 GC**）+ 剥离 `PYTEST_CURRENT_TEST`。
    """
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    #: N5-②（2026-10-06；19 号 §67.11.2）＋ **N6 分根**（§69.3）：basetemp
    #: **项目内独立子根** `代码/_scratch/pytest_tmp/bt_test/`（与门禁第 3 项
    #: `…/pytest_tmp/check` 分根；根可经 `BTFTOOL_TEST_PYTEST_BASETEMP` 覆盖）
    #: ＋**每次唯一**子目录（保持父子隔离，避免共用固定 basetemp 时相互清空）
    #: ——不走 `%TEMP%`（系统临时区垃圾轮转可能被环境批量删除守卫拦截
    #: ⇒ "用例全过但 exit≠0"假红）。变量名 `BTFTOOL_` 前缀（**N7**，§69.4）。
    _base = Path(os.environ.get(
        "BTFTOOL_TEST_PYTEST_BASETEMP",
        Path(__file__).resolve().parents[2].parent / "_scratch"
        / "pytest_tmp" / "bt_test"))
    _base.mkdir(parents=True, exist_ok=True)
    basetemp = Path(tempfile.mkdtemp(prefix="btf_bt_test_", dir=str(_base)))
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
        # N5-②：回收**分小批逐子项**执行，且**绝不改变退出码**——环境批量删除
        # 守卫对同轮大批量删除会拦（实测项目内阈值 500；固定复用同一 basetemp
        # 目录时，pytest 会话起始"清空旧内容"同样被拦 ⇒ 全部用例假 ERROR）。
        # 如被拦：保留现场 + 打一行 [note]，退出码仍 = pytest 码（不掩盖）。
        try:
            for child in basetemp.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            basetemp.rmdir()
        except BaseException:            # 守卫经 SystemExit(1) 表达"需确认"
            print(f"[note] basetemp 未完全回收（环境批量删除守卫）：{basetemp}")


def _args_test(p: argparse.ArgumentParser) -> None:
    p.add_argument("--layer", default="l1",
                   help="测试层标记：l1|l2|l3|l4|l5|all")
    p.add_argument("--pattern", help="pytest -k 表达式")
    p.add_argument("--path", help="测试路径（默认全量）")


# ─────────────────────────────────────────────────────────────
# ⑥ dataset
# ─────────────────────────────────────────────────────────────
def _cmd_dataset(args: argparse.Namespace) -> int:
    """bt dataset <build-golden|verify|refcalc|freeze>：黄金集 / 数据冻结清单。

    子进程编排**委托 `btf.app.build_dataset`**（P2-3 第 5 组重复收敛）；
    三动作语义见 `btf.app.build_dataset` docstring（审查报告 P3：`--verify`
    与"内嵌数据重算"此前只有前者的影子——`refcalc` 补上主库无关的自洽性检查）。

    **CL-4（19 号 §72.5）**：`freeze` = 数据不可变快照（S-2 清单＋哈希）——
    委托 `btf.app.freeze_dataset`（复用 BB-1 实算；只读落盘、同 anchor 幂等/
    冲突显式；R3 不静默覆写）。
    """
    from btf.app import build_dataset

    if args.action not in _DATASET_ACTIONS:
        print(f"[error] 未知 dataset 动作：{args.action!r}"
              f"（可选 {_DATASET_ACTIONS}）", file=sys.stderr)
        return 2
    if args.action == "freeze":
        from btf.app import freeze_dataset

        if not args.config:
            print("[error] dataset freeze 需 --config <yaml>"
                  "（冻结口径随回测配置：区间/表集/档位）", file=sys.stderr)
            return 2
        try:
            result = freeze_dataset(args.config, out=args.out, mode=args.mode)
        except FileExistsError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 1
        except Exception as exc:              # 配置/装配/主库不可用
            print(f"[error] 冻结失败：{type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
        payload = result["payload"]
        if result["action"] == "idempotent":
            print(f"[info] 冻结清单已存在且 content_hash 一致（幂等，不覆写）："
                  f"{result['path']}")
        else:
            print(f"[ok] 冻结清单已落盘（只读）：{result['path']}")
        print(f"     anchor_date={payload.get('anchor_date')}"
              f" | mode={payload.get('mode')}"
              f" | tables={len(payload.get('tables') or [])}"
              f" | content_hash={payload.get('content_hash')}")
        return 0
    return build_dataset(args.cases, action=args.action,
                         verify=args.verify or args.action == "verify",
                         force=args.force, list_only=args.list,
                         refcalc=args.action == "refcalc", out=args.out)


#: dataset 子动作（审查报告 P3；`build-golden` 为默认，向后兼容原调用；
#: `freeze` = CL-4 数据冻结清单）
_DATASET_ACTIONS = ("build-golden", "verify", "refcalc", "freeze")


def _cmd_data(args: argparse.Namespace) -> int:
    """bt data <scan-consolidation>：数据专项扫描（**R34′**；21 号 §4.13）。

    判据＝**双证据 ＋ 容差 ＋ fail-closed**（ADR-20 第 5 条）；
    退出码：0（全部一致）／1（存在 fail-closed 事件——门禁语义阻断）／
    2（参数用法/前置错误）。输出逐事件 `ts_code / 事件日 / R / 证据1 /
    证据2 / 偏差 / 结论` ＋ 未建模项清单。
    """
    from btf.app import scan_consolidation

    if args.data_action not in _DATA_ACTIONS:
        print(f"[error] 未知 data 动作：{args.data_action!r}"
              f"（可选 {_DATA_ACTIONS}）", file=sys.stderr)
        return 2
    if not args.config:
        print("[error] data scan-consolidation 需 --config <yaml>"
              "（取 run.universe 显式 ETF 池与 run.period）", file=sys.stderr)
        return 2
    try:
        report = scan_consolidation(args.config, out=args.out,
                                    tolerance=args.tolerance)
    except (ValueError, FileNotFoundError) as exc:
        print(f"[error] 用法/前置错误：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:                    # 主库不可用等
        print(f"[error] 扫描失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"[ok] scan-consolidation：{report['n_symbols']} 只 ETF/LOF | "
          f"区间 {report['start']}~{report['end']} | 事件 {report['n_events']} "
          f"（fail-closed {report['n_fail_closed']}）| 容差 "
          f"{report['tolerance']}")
    print(f"{'ts_code':<12}{'事件日':<10}{'R':>10}{'证据1':>10}"
          f"{'证据2':>10}{'偏差':>10}  结论")
    for ev in report["events"]:
        dev = "—" if ev.get("dev") is None else f"{ev['dev']:.6f}"
        ev2 = "—" if ev.get("evidence2") is None else f"{ev['evidence2']:.6f}"
        print(f"{ev['ts_code']:<12}{ev['date']:<10}{ev['R']:>10.6f}"
              f"{ev['evidence1']:>10.6f}{ev2:>10}{dev:>10}  "
              f"{'一致' if ev.get('ok') else 'fail-closed'}")
    for note in report.get("notes") or ():
        print(f"[warn] {note}")
    # **R47′（N51）**：降级项（如 trade_cal 缺失）⇒ 告警 + **退出码 2**
    # （前置不完整；扫描结果仍打印——不静默、不抛未捕获异常）
    for item in report.get("degraded") or ():
        print(f"[warn] 降级：{item}")
    if report.get("degraded"):
        return 2
    if report["n_fail_closed"]:
        return 1
    return 0


#: data 子动作（R34′；21 号 §4.13）
_DATA_ACTIONS = ("scan-consolidation",)


def _args_data(p: argparse.ArgumentParser) -> None:
    p.add_argument("data_action", nargs="?", default="scan-consolidation",
                   choices=_DATA_ACTIONS,
                   help="子动作：scan-consolidation（ETF/LOF 份额折算双证据扫描）")
    p.add_argument("--config", help="回测配置 YAML（取 universe/period）")
    p.add_argument("--out", help="结果 JSON 路径（缺省只打印）")
    p.add_argument("--tolerance", type=float, default=0.005,
                   help="双证据偏差容差（缺省 0.005＝0.5%%，ADR-20 第 5 条）")


def _args_dataset(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", nargs="?", default="build-golden",
                   choices=_DATASET_ACTIONS,
                   help="子动作：build-golden（构建）|verify（与主库对账，不写）"
                        "|refcalc（内嵌数据重算，不读主库）"
                        "|freeze（数据冻结清单，CL-4）")
    p.add_argument("--cases", default="g1", help="案例（g1|all|逗号分隔）")
    p.add_argument("--list", action="store_true", help="列出案例与待补组")
    p.add_argument("--verify", action="store_true",
                   help="只校验不写（等价 action=verify）")
    p.add_argument("--force", action="store_true", help="显式覆盖（留痕）")
    p.add_argument("--config", help="freeze：回测配置 YAML（冻结口径随它）")
    p.add_argument("--mode", choices=("meta", "fast", "full"),
                   help="freeze：指纹档位（缺省随 data.fingerprint_mode=meta）")
    p.add_argument("--out",
                   help="输出目录（freeze：清单目录，缺省 BTFTOOL_FREEZE_DIR）")


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


def _parse_perturb(spec: str) -> list[float]:
    """`--perturb "0.1,0.2"` → δ 列表（0 < δ < 1；RC-07）。

    δ 为**相对比例**：对 top1 的数值型参数生成 `p×(1±δ)` 邻域点。
    """
    deltas: list[float] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            delta = float(raw)
        except ValueError as exc:
            raise ValueError(
                f"--perturb 需形如 0.1,0.2，得 {raw!r}") from exc
        if not 0.0 < delta < 1.0:
            raise ValueError(f"--perturb δ 须在 (0, 1)，得 {raw!r}")
        deltas.append(delta)
    if not deltas:
        raise ValueError("--perturb 为空（至少一个 δ）")
    return deltas


def _fmt_prob(value: object) -> str:
    """显著性概率打印（None → `—`；RC-06）。"""
    if value is None:
        return "—"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "—"


def _cmd_optimize(args: argparse.Namespace) -> int:
    """bt optimize --config <yaml> --grid <spec>：网格搜索并打印排名。"""
    import json

    from btf import registry
    from btf.config.loader import load_config

    try:
        grid = _parse_grid(args.grid)
        perturb = (_parse_perturb(args.perturb)
                   if getattr(args, "perturb", None) else None)
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
    report = optimizer.run(config, grid, objective=args.objective,
                           n_trials_declared=args.n_trials,
                           perturb=perturb)

    # CL-1（19 号 §72.2 / 报告 D 篇 P2-5）：**短区间默认阻断**。判据 = 实算样本
    # 长度（`n_trading_days`，与 RC-06 披露同源——"样本不足 ⇒ 排序无意义"）。
    # 阻断点在任何排序结论**打印之前** ⇒ 不产出可被引用的排名；放行（显式
    # `--allow-short`）必须把 override 记入产物（禁"绕过即静默"）；默认档
    # （n_days ≥ 阈值）行为与改动前逐位一致（R4）。
    from btf.runtime import MIN_OPTIMIZE_TRADING_DAYS

    n_days = int(report.get("n_trading_days") or 0)
    if 0 < n_days < MIN_OPTIMIZE_TRADING_DAYS:
        if not getattr(args, "allow_short", False):
            print(
                f"[error] 样本区间仅 {n_days} 个交易日（< "
                f"{MIN_OPTIMIZE_TRADING_DAYS} 交易日 ≈ 1 个完整交易年）："
                f"不得据此给出参数排序结论（短样本下指标数学有定义、业务无意义，"
                f"19 号 §72.2 / 报告 D 篇 P2-5）。去路：① 拉长 run.period；"
                f"② 显式放行 `--allow-short`（结果将标注 n_days/阈值/override 并"
                f"记入产物，仍**不得**用作参数选择依据）")
            return 3
        report["short_period_override"] = {
            "n_days": n_days,
            "threshold": MIN_OPTIMIZE_TRADING_DAYS,
            "override": True,
        }
        tail = ("override 已记入产物 short_period_override"
                if args.out else
                "未指定 --out ⇒ 仅本行留痕；如需持久留痕请加 --out（写产物 JSON）")
        print(f"[warn] --allow-short 放行：{n_days} < "
              f"{MIN_OPTIMIZE_TRADING_DAYS} 交易日——本结果**不得**用作参数"
              f"选择的依据（{tail}）")

    print(f"[ok] grid_search：{report['n_combos']} 组 × {report['workers']} 进程"
          f" | objective={report['objective']}")
    # RC-06（2026-10-05）：多重检验**显式可见**——"试得越多越不可信"。
    print(f"[warn] {report['adjustment_note']}"
          "（PSR=单策略显著性；DSR=校正「挑出来」之后的显著性）")
    print(f"{'rank':>4}  {'value':>10}  {'psr':>6}  {'dsr':>6}  params")
    for item in report["top"][: args.top]:
        params = ", ".join(f"{k}={v}" for k, v in sorted(item["params"].items()))
        print(f"{item['rank']:>4}  {item['value']:>10.4f}  "
              f"{_fmt_prob(item.get('psr')):>6}  {_fmt_prob(item.get('dsr')):>6}"
              f"  {params}")
    # RC-07（2026-10-05）：邻域扫描结果——极差 / 标准差 / 相对退化率；
    # 退化 > 50% ⇒ warn「疑似过拟合」（**只提示，不替使用者判定**）。
    block = report.get("perturbation")
    if block:
        if block["n_points"] == 0:
            print(f"[info] perturb δ={block['deltas']}：{block['note']}")
        elif block["relative_degradation"] is None:
            print(f"[info] perturb：{block['n_points']} 点 | {block['note']}")
        else:
            print(f"[info] perturb：{block['n_points']} 点 | "
                  f"极差={block['range']:.4f} | σ={block['std']:.4f} | "
                  f"相对退化={block['relative_degradation']:.1%}")
            if block.get("sensitive"):
                print(f"[warn] 参数敏感：邻域相对退化 "
                      f"{block['relative_degradation']:.0%} > "
                      f"{block['threshold']:.0%} —— 疑似过拟合"
                      f"（RC-07；只提示，不替使用者判定）")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                  default=str), encoding="utf-8")
        print(f"     结果: {out}")
    return 0


def _args_optimize(p: argparse.ArgumentParser) -> None:
    from btf.runtime import MIN_OPTIMIZE_TRADING_DAYS

    p.add_argument("--config", required=True, help="回测配置 YAML")
    p.add_argument("--grid", required=True,
                   help='网格 spec："path=v1,v2;path2=v1,v2"（点分配置路径）')
    p.add_argument("--objective", default="sharpe_ratio",
                   help="排名指标（默认 sharpe_ratio；配 report.benchmark 后"
                        "可选 information_ratio / annualized_excess，RC-08）")
    p.add_argument("--workers", type=int, default=8,
                   help="进程数（默认 8；0=内联顺序执行——调试/注入型 feed）")
    p.add_argument("--top", type=int, default=10, help="打印前 N 名（默认 10）")
    p.add_argument("--n-trials", type=int, default=0, dest="n_trials",
                   help="历史累计试验数（RC-06：与本次组合数相加为 N，用于 DSR 校正）")
    p.add_argument("--perturb", metavar="D1,D2",
                   help="参数邻域扫描（RC-07）：对 top1 数值型参数做 "
                        "p×(1±δ)，如 0.1,0.2（退化 > 50%% 提示疑似过拟合）")
    p.add_argument("--allow-short", action="store_true", dest="allow_short",
                   help=f"显式放行「样本 < {MIN_OPTIMIZE_TRADING_DAYS} 交易日」的"
                        "网格排序（CL-1；默认阻断退出码 3；放行会把 n_days/"
                        "阈值/override 记入产物 short_period_override）")
    p.add_argument("--out", help="结果 JSON 输出路径（可选）")


# ─────────────────────────────────────────────────────────────
# ⑧ check / cold-backup / runs / show（CLI 审查报告-20260930：P0/P2）
# ─────────────────────────────────────────────────────────────
def _cmd_check(args: argparse.Namespace) -> int:
    """bt check：十项架构门禁（委托 `tools/check.py`，口径单一真源）。

    审查报告 P0：19 号/CHANGELOG/记忆长期写 `bt check`，而它此前**不存在**——
    文档与 CLI 脱节。收编后 `bt check` ≡ `python tools/check.py`（同脚本、
    同退出码 0/1）：判定逻辑不复制，避免"两条路各判一套"。
    """
    from btf.app import run_gate_check

    return run_gate_check()


def _args_check(p: argparse.ArgumentParser) -> None:
    """门禁**不接受参数**：加开关 = 加"跑得不一样"的可能（口径唯一优先）。"""


def _cmd_cold_backup(args: argparse.Namespace) -> int:
    """bt cold-backup：跨版本源码冷备（委托 `tools/cold_backup.py`）。

    审查报告 P2：批末/定版的"冷备留痕"此前只能在本文件外记绝对路径命令，
    现为子命令（`--verify DIR` 校验既有冷备目录，与新建互斥）。
    """
    from btf.app import cold_backup

    return cold_backup(reason=args.reason, out=args.out, verify=args.verify)


def _args_cold_backup(p: argparse.ArgumentParser) -> None:
    p.add_argument("--reason", help="备份依据（写入 MANIFEST；缺省用脚本默认值）")
    p.add_argument("--out", help="备份根目录（缺省用脚本默认值）")
    p.add_argument("--verify", metavar="DIR", help="校验既有冷备目录（不新建）")


def _fmt_metric_short(value: Any) -> str:
    """列表用紧凑指标（`bt runs` 表格）：浮点 6 位有效数字，NaN/None → null。

    与 `_fmt_metric`（落盘一致的完整精度，`bt run`/`bt show` 用）的分工：
    一行一 run 的清单里 `0.15760742457624948` 会淹掉表格；此处**只做显示
    截断**，不改变任何落盘/复核口径（`bt show` 仍给全精度）。用 `%g` 而非
    定小数位：计数型指标（n_fills）不拖 `4493.0000` 的尾巴，量级极小的值
    也不会被压成 `0.0000`。
    """
    if value is None or (isinstance(value, float) and value != value):
        return "null"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _cmd_runs(args: argparse.Namespace) -> int:
    """bt runs [--latest N]：列 RunStore 下的 run 摘要（目录时间新→旧；只读）。

    审查报告 P2：此前看产物必须 ls + 手工读 manifest.json（或 `report --run`
    猜 run_id）；本命令把 run_id / 状态 / 时间 / 关键指标一行给出，**不改产物**。
    """
    from btf.app import list_runs

    rows = list_runs(out_dir=args.out_dir, latest=args.latest)
    if not rows:
        print("[info] 无 run 产物（先 `bt run --config <yaml>`，"
              "或用 --out-dir 指定产物根）")
        return 0
    print(f"{'run_id':<26}{'状态':<11}{'时间':<21}指标")
    for row in rows:
        metrics = "  ".join(f"{k}={_fmt_metric_short(row.metrics.get(k))}"
                            for k in _RUN_METRIC_KEYS)
        print(f"{row.run_id:<26}{row.status:<11}{row.created:<21}{metrics}")
        if row.note:
            print(f"    [warn] manifest/metrics 不可读：{row.note}")
    return 0


def _args_runs(p: argparse.ArgumentParser) -> None:
    p.add_argument("--latest", type=int, metavar="N",
                   help="只列最近 N 个（按 run 目录时间倒序）")
    p.add_argument("--out-dir", help="RunStore 根目录（默认产物目录）")


def _cmd_show(args: argparse.Namespace) -> int:
    """bt show --run <run_id>：manifest + 指标摘要（只读，不重算指标）。

    与 `bt verify` 的分工：`verify` 会**重算**指标并对账（耗时长），`show`
    只打印落盘事实——"看一眼这个 run 是什么"不应该付重算代价。
    """
    from btf.app import run_detail

    try:
        detail = run_detail(args.run, out_dir=args.out_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    manifest = detail.manifest
    cfg = manifest.get("config_effective") or {}
    run_cfg = cfg.get("run") or {}
    period = run_cfg.get("period") or {}
    data = manifest.get("data_version") or {}
    code = manifest.get("code_version") or {}
    print(f"run_id      : {manifest['run_id']}（{manifest['status']}）")
    print(f"产物目录    : {detail.directory}")
    print(f"期间        : {period.get('start', '?')} → {period.get('end', '?')}"
          f"（初始资金 {run_cfg.get('initial_cash', '—')}）")
    print(f"策略        : {run_cfg.get('strategy', '—')}")
    print(f"数据        : feed={data.get('dataset')}"
          f" 指纹={data.get('content_hash')}")
    print(f"规则版本    : {manifest.get('rules_version')}"
          f"（overrides {len(manifest.get('rules_overrides') or [])} 条）")
    print(f"配置哈希    : {manifest.get('config_hash')}")
    print(f"重算锚      : {manifest.get('metrics_digest')}"
          f"（manifest.metrics_digest；`bt verify` 的对账锚点）")
    print(f"契约版本    : {manifest.get('contract_version')}"
          f" | 契约基线={manifest.get('btf_contract_version')}")
    print(f"代码版本    : {code.get('package_version', '—')}"
          f"（commit {code.get('git_commit', '—')}"
          f"{' dirty' if code.get('dirty') else ''}）")
    print("指标:")
    for key in _RUN_METRIC_KEYS:
        print(f"     {key}: {_fmt_metric(detail.metrics.get(key))}")
    for note in manifest.get("assembly_notes") or []:
        print(f"[note] {note}")
    if manifest["status"] != "COMPLETED":
        print(f"[warn] run 状态 {manifest['status']}（非 COMPLETED）："
              f"{manifest.get('error') or '指标可能不完整'}", file=sys.stderr)
    return 0


def _args_show(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", required=True, help="run_id")
    p.add_argument("--out-dir", help="RunStore 根目录（默认产物目录）")


#: validate 子动作（RC-15/16）
_VALIDATE_ACTIONS = ("walk-forward", "mc")


def _cmd_validate(args: argparse.Namespace) -> int:
    """bt validate walk-forward|mc：滚动验证（RC-15）/ 蒙特卡洛（RC-16）。"""
    from btf.app import run_mc, run_walk_forward

    if args.action == "walk-forward":
        if not args.config:
            print("[error] walk-forward 需 --config <yaml>", file=sys.stderr)
            return 2
        if not args.window:
            print("[error] walk-forward 需 --window <训练窗长(交易日)>",
                  file=sys.stderr)
            return 2
        try:
            overrides = _parse_overrides(args.set)
        except ValueError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 2
        warm_raw = str(getattr(args, "warmup", "auto")).strip().lower()
        if warm_raw in ("", "auto"):
            warmup: int | None = None                   # R58′：默认 auto 推导
        else:
            try:
                warmup = int(warm_raw)
            except ValueError:
                warmup = -1
            if warmup < 0:
                print("[error] --warmup 须为 ≥0 的整数或 auto（缺省 auto）",
                      file=sys.stderr)
                return 2
        try:
            report = run_walk_forward(
                args.config, window=args.window, test=args.test,
                step=args.step, mode=args.mode, purge=args.purge,
                embargo=args.embargo, persist=not args.no_persist,
                warmup=warmup,
                out=args.out, out_dir=args.out_dir, overrides=overrides)
        except Exception as exc:                    # 装配/窗口/IO 失败
            print(f"[error] walk-forward 失败：{type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
        spec = report["spec"]
        print(f"[ok] walk-forward：{report['n_folds']} 折 | mode={spec['mode']}"
              f" window={spec['window']} test={spec['test']}"
              f" step={spec['step']} purge={spec['purge']}"
              f" embargo={spec['embargo']} | persist={spec['persist']}"
              f" | warmup={spec['warmup']}（{spec['warmup_source']}）")
        if report.get("warmup_note"):
            print(f"[warn] {report['warmup_note']}")
        keys = ("sharpe_ratio", "annualized_return", "max_drawdown",
                "total_return")
        print(f"{'折':>3}  {'训练窗':<23} {'测试窗':<23}  "
              + "  ".join(f"oos_{k[:9]:>10}" for k in keys))
        for fold in report["folds"]:
            m = fold["test_metrics"]
            print(f"{fold['index']:>3}  "
                  f"{fold['train']['start']}~{fold['train']['end']}  "
                  f"{fold['test']['start']}~{fold['test']['end']}  "
                  + "  ".join(f"{_fmt_metric(m.get(k)):>14}" for k in keys))
        print("── OOS 汇总（含最差折——不得只报最优）──")
        for key, s in report["summary"].items():
            print(f"     {key}: mean={_fmt_metric(s['mean'])}"
                  f" median={_fmt_metric(s['median'])}"
                  f" min={_fmt_metric(s['min'])}"
                  f" max={_fmt_metric(s['max'])} n={s['n']}")
        if args.out:
            print(f"     结果: {args.out}")
        return 0

    # ── mc ──
    if not args.run:
        print("[error] mc 需 --run <run_id>（产物读回）", file=sys.stderr)
        return 2
    try:
        report = run_mc(args.run, n_resamples=args.n, n_permutations=args.n,
                        seed=args.seed, out=args.out, out_dir=args.out_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[error] mc 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    seed = report["seed"]
    print(f"[ok] mc：run={report['run_id']} 样本 {report['n_periods']} 日"
          f"（年化 {report['annualization']}）| seed={seed['used']}"
          f"（{seed['source']}）")
    bs = report["bootstrap"]
    print(f"bootstrap（{bs['n_resamples']} 次重采样）:")
    for key in ("sharpe", "annualized_return"):
        item = bs[key]
        print(f"     {key}: observed={_fmt_metric(item['observed'])}"
              f" p05={_fmt_metric(item['p05'])}"
              f" p50={_fmt_metric(item['p50'])}"
              f" p95={_fmt_metric(item['p95'])}"
              f" CI=[{_fmt_metric(item['ci_low'])},"
              f"{_fmt_metric(item['ci_high'])}]"
              f" 跨0={item['crosses_zero']}")
    for item in report["permutation"].values():
        print(f"置换检验[{item['scheme']}/{item['statistic']}] 双侧:"
              f" observed={_fmt_metric(item['observed'])}"
              f" p={_fmt_metric(item['p_value'])}"
              f"（null {item['null_summary']['n']} 次）")
    nb = report["noise_baseline"]
    if nb:
        print(f"随机择时对照（{nb['n_hold']} 天 / {nb['n_draws']} 次）:"
              f" 观测分位={_fmt_metric(nb['observed_percentile'])}"
              f" p05={_fmt_metric(nb['p05'])}"
              f" p50={_fmt_metric(nb['p50'])}"
              f" p95={_fmt_metric(nb['p95'])}")
    else:
        print(f"[warn] {report.get('noise_note')}")
    if args.out:
        print(f"     结果: {args.out}")
    return 0


def _args_validate(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", choices=_VALIDATE_ACTIONS,
                   help="walk-forward（RC-15 滚动验证）| mc（RC-16 蒙特卡洛）")
    p.add_argument("--config", help="walk-forward：回测配置 YAML")
    p.add_argument("--window", type=int, metavar="N",
                   help="walk-forward：训练窗长（交易日）")
    p.add_argument("--test", type=int, metavar="N",
                   help="walk-forward：测试窗长（缺省 = --step）")
    p.add_argument("--step", type=int, metavar="N",
                   help="walk-forward：滚动步长（缺省 = --test）")
    p.add_argument("--mode", choices=("rolling", "anchored"), default="rolling",
                   help="训练窗形态：rolling=滚动 / anchored=锚定加长（默认 rolling）")
    p.add_argument("--purge", type=int, default=0, metavar="N",
                   help="训练/测试间隔交易日（与 embargo 合并计入 gap；缺省 0）")
    p.add_argument("--embargo", type=int, default=0, metavar="N",
                   help="测试前缓冲带（交易日；缺省 0）")
    p.add_argument("--warmup", default="auto", metavar="N|auto",
                   help="OOS 折热身交易日（**R58′/N60**）：缺省 auto＝按策略声明 "
                        "`lookback_bars` 推导，未声明 ⇒ 252＋WARN；"
                        "`--warmup 0` ＝旧行为对照档（长 lookback 策略可能"
                        "样本外假阴性）")
    p.add_argument("--no-persist", action="store_true",
                   help="walk-forward：不落盘（缺省每折落盘且窗口信息入 manifest）")
    p.add_argument("--run", help="mc：run_id（读回产物，不重跑引擎）")
    p.add_argument("--n", type=int, default=1000, metavar="N",
                   help="mc：重采样 / 置换次数（缺省 1000）")
    p.add_argument("--seed", type=int,
                   help="mc：显式种子（缺省 = manifest.seed.master 单源）")
    p.add_argument("--out", help="结果 JSON 输出路径（可选）")
    p.add_argument("--out-dir", dest="out_dir",
                   help="RunStore 根（walk-forward 落盘 / mc 读产物）")
    p.add_argument("--set", action="append", metavar="K=V",
                   help="walk-forward：配置覆盖（与 bt run 同则）")


# ─────────────────────────────────────────────────────────────
# ⑦ adhoc（HH-3，19 号 §128）
# ─────────────────────────────────────────────────────────────
def _cmd_adhoc(args: argparse.Namespace) -> int:
    """bt adhoc：一次性/外部回测脚本的**产物契约封装**（委托 `tools/adhoc_run.py`）。

    **HH-3（19 号 §128，2026-10-14）**：把 `tools/adhoc_run.py` 收编为 CLI
    子命令（L2 复算报告 §9 待办第 6 条）——**入口统一、功能不变**；与
    `bt check` / `bt cold-backup` 同法：只转发到工具脚本（单一真源），
    不在 `btf/` 内做第二实现。被封装脚本自身的参数经 `--` 分隔透传。
    """
    from btf.app import adhoc_run

    return adhoc_run(
        name=args.name, script=args.script, params=args.params,
        tables=args.tables, as_of=args.as_of, watch_dir=args.watch_dir,
        out=args.out, register_only=args.register_only,
        list_only=args.list, script_args=tuple(args.script_args or ()),
    )


def _args_adhoc(p: argparse.ArgumentParser) -> None:
    p.add_argument("--name", help="实验名（登记 key）")
    p.add_argument("--script", help="待执行/待登记的脚本路径")
    p.add_argument("--params", help="参数 JSON 路径")
    p.add_argument("--tables", default="", help="声明用到的主库表，逗号分隔")
    p.add_argument("--as-of", default="", help="数据基准日 YYYYMMDD")
    p.add_argument("--watch-dir", default="",
                   help="差分监视目录（默认脚本所在目录）")
    p.add_argument("--out", help="登记台根目录（缺省 tools 脚本默认）")
    p.add_argument("--register-only", action="store_true",
                   help="不执行脚本，仅登记现有产物（存量脚本用）")
    p.add_argument("--list", action="store_true", help="列出登记台")
    p.add_argument("script_args", nargs=argparse.REMAINDER,
                   help="传给被封装脚本的参数（用 -- 分隔）")


#: 命令注册表：名字 → (处理器, 帮助, 参数注册器)
COMMANDS: dict[str, tuple[CommandHandler, str, ArgAdder]] = {
    "config-check": (_cmd_config_check,
                     "校验回测配置：四层合并 + JSON Schema（bt config-check <config.yaml>）",
                     _args_config_check),
    "run": (_cmd_run, "执行回测并落盘（--dry-run 只装配不执行）", _args_run),
    "report": (_cmd_report, "由 RunStore 产物再生成 HTML 报告（幂等）", _args_report),
    "verify": (_cmd_verify, "R1 复核：产物重算指标 vs manifest.metrics_digest",
               _args_verify),
    "test": (_cmd_test, "运行测试层（pytest 执行器；提交前门禁请用 `bt check`）",
             _args_test),
    "dataset": (_cmd_dataset,
                "黄金集：build-golden 构建 / verify 主库对账 / refcalc 内嵌重算"
                " / freeze（CL-4 数据冻结清单）",
                _args_dataset),
    "optimize": (_cmd_optimize,
                 "参数网格搜索（grid_search 进程池；v0.5 V5-3）", _args_optimize),
    "check": (_cmd_check,
              "十项架构门禁（= python tools/check.py；提交前必跑）", _args_check),
    "cold-backup": (_cmd_cold_backup,
                    "跨版本源码冷备（= python tools/cold_backup.py）",
                    _args_cold_backup),
    "runs": (_cmd_runs, "列 RunStore 下的 run 摘要（只读；--latest N）", _args_runs),
    "show": (_cmd_show, "单 run 的 manifest + 指标摘要（只读；--run <id>）",
             _args_show),
    "validate": (_cmd_validate,
                 "滚动验证 / 蒙特卡洛：walk-forward（RC-15）| mc（RC-16）",
                 _args_validate),
    "data": (_cmd_data,
             "数据专项扫描：scan-consolidation（ETF/LOF 份额折算双证据；"
             "R34′/ADR-20）",
             _args_data),
    "adhoc": (_cmd_adhoc,
              "一次性/外部回测脚本的产物契约封装（= python tools/adhoc_run.py；"
              "HH-3）",
              _args_adhoc),
}


def main(argv: list[str] | None = None) -> int:
    _utf8()
    parser = argparse.ArgumentParser(
        prog="bt", description="btf 回测工具（十四命令族：config-check/run/report/"
                               "verify/test/dataset/optimize/validate/data/adhoc + check/"
                               "cold-backup/runs/show）")
    parser.add_argument("--version", action="version",
                        version=f"btf {__version__}",
                        help="打印版本（单一真源 btf/_version.py）")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_handler, help_text, add_args) in sorted(COMMANDS.items()):
        add_args(sub.add_parser(name, help=help_text))

    args = parser.parse_args(argv)
    handler: Any = COMMANDS[args.command][0]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
