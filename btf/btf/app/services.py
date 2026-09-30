# -*- coding: utf-8 -*-
"""应用服务层：CLI 与 API **共用**的编排逻辑（P2-3 / R4；19 号 §9.3）。

为什么需要（审计原话）：`cli/main.py` 与 `api.py` 存在 **8 组逐字重复**——
环境过滤、`--set` 类型推断、报告装配、dataset 子进程编排、`compute_all`
编排、日期 helper、嵌套路径写入……"改一处需同步多处"，且 api.py 承诺与
CLI **字节等价**（`api.py` 头部）→ 重复即分叉风险。

本模块是该承诺的落地形态：**唯一实现**放在 app，两个入口层只做参数解析与
转发（铁律新 10：cli/api 不得含编排逻辑）。已收敛的重复组：

| # | 重复组 | 原位置 | 现位置 |
|---|---|---|---|
| 1 | `_filtered_env` | `cli/main.py:34` ↔ `api.py:26` | `filter_btf_env()` |
| 2 | `--set` 嵌套解析 + 类型推断 | `cli/main.py:156-175` ↔ `grid.py` 第三份 | `parse_overrides()` / `parse_override_value()` |
| 3 | run 主链路（load→build→run） | `cli/main.py:186-214` ↔ `api.py:46-53` | `run_backtest()` |
| 4 | 报告装配（Assumptions+Builder+写盘） | `cli/main.py:260-285` ↔ `api.py:56-83` | `build_report_file()` |
| 5 | dataset 子进程编排 | `cli/main.py` ↔ `api.py:86-111` | `build_dataset()` |

**未收敛（明确留待后续批次）**：`compute_all` 三处调用链（runtime×2 +
grid）、日期 helper（runtime/store/state 各一）——二者与 R4 的
`BTFRuntime` 拆分同一批，见 19 号 §41.5 结论。
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = [
    "RunDetail",
    "RunOutcome",
    "RunSummary",
    "assemble_assumptions",
    "build_dataset",
    "build_report_file",
    "cold_backup",
    "filter_btf_env",
    "list_runs",
    "parse_override_value",
    "parse_overrides",
    "run_backtest",
    "run_detail",
    "run_gate_check",
]


@dataclass(frozen=True)
class RunOutcome:
    """一次回测编排的产物（runtime 供 CLI 披露用；result 供入口返回值）。

    `result is None` ⇔ dry-run（只装配）。
    """

    runtime: Any
    result: Any | None


def filter_btf_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """只把 `BTF_*` 环境变量交给配置层（测试/部署隔离；原两处逐字重复）。"""
    src = os.environ if environ is None else environ
    return {k: v for k, v in src.items() if k.startswith("BTF_")}


def parse_override_value(raw: str) -> Any:
    """CLI 字符串 → 配置值（**单源类型推断**：int → float → str）。

    与 `config.loader` 的合并层同口径（配置层负责最终类型校验）；`true/false`
    等布尔由配置层/`--set` 语义决定（此处不做隐式布尔化——避免 "1" 被误读）。
    """
    for caster in (int, float):
        try:
            return caster(raw)
        except ValueError:
            continue
    return raw


def parse_overrides(pairs: Sequence[str] | None) -> dict[str, Any]:
    """`--set a__b=1`（`__` 分隔嵌套）→ 覆盖 dict（L4 CLI 层）。"""
    out: dict[str, Any] = {}
    for pair in pairs or []:
        key, _, raw = pair.partition("=")
        if not key or not raw:
            raise ValueError(f"--set 需形如 a.b=1，得 {pair!r}")
        cursor = out
        parts = key.split("__")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = parse_override_value(raw)
    return out


def run_backtest(
    config: str | Path | Mapping[str, Any],
    *,
    overrides: Mapping[str, Any] | None = None,
    out: str | Path | None = None,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
) -> RunOutcome:
    """主链路（CLI/api 同源）：load_config → build → run（落盘）。

    返回 `RunOutcome`（runtime + result）：CLI 需要 runtime 打 `--verbose`
    分步耗时/披露摘要，api 只需要 result —— 同一次装配**不分叉**。
    `dry_run=True` → `result is None`（镜像 `bt run --dry-run`）。
    """
    from btf.runtime import BTFRuntime, make_store

    rt = BTFRuntime().load_config(config, environ=filter_btf_env(environ),
                                  overrides=overrides)
    rt.build()
    if dry_run:
        return RunOutcome(runtime=rt, result=None)
    return RunOutcome(runtime=rt, result=rt.run(store=make_store(out)))


def assemble_assumptions(store: Any, bundle: Any) -> Any:
    """报告「假设与披露」装配（费率分段/规则版本/降级事件；CLI/api 同源）。"""
    from btf.runtime import fee_segments
    from btf.viz.report import Assumptions

    degraded = ("事件日志已采样（replay 不完整）"
                if store.events_sampled(bundle.manifest.run_id) else "")
    return Assumptions(
        matching="next_open（T+1 开盘撮合）", slippage="none（回测基线）",
        fee_segments=tuple(fee_segments()),
        rules_version=bundle.manifest.rules_version,
        rules_overrides=tuple(bundle.manifest.rules_overrides),
        degraded=(degraded,) if degraded else (),
    )


def build_report_file(
    run_id: str,
    *,
    out: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> Path:
    """由产物再生成 HTML 报告（幂等；返回写盘路径）。"""
    from btf.runtime import BTFRuntime, make_store
    from btf.viz.report import ReportBuilder

    store = make_store(out_dir)
    bundle = BTFRuntime().load_run(run_id, store=store)
    html = ReportBuilder(bundle, assemble_assumptions(store, bundle)).build()
    target = (Path(out) if out
              else Path(store.run_dir(run_id)) / "report.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


def build_dataset(
    cases: str = "g1",
    *,
    action: str = "build-golden",
    verify: bool = False,
    force: bool = False,
    list_only: bool = False,
    refcalc: bool = False,
    out: str | Path | None = None,
) -> int:
    """黄金集构建（委托 `tools/build_golden.py` 子进程；CLI/api 同源）。

    三个动作（CLI 审查报告-20260930 P3：把"构建/校验/重算"三条路显式化）：
        build-golden  构建（写盘；`force` 显式覆盖）
        verify        只与**主库**对账、不写盘（`--verify`）
        refcalc       只用**案例文件内嵌数据**独立重算断言（`--refcalc`，
                      不读主库——"黄金集自洽性"与"主库一致性"分开可证）
    """
    args: list[str] = []
    if refcalc:
        args += ["--cases", cases, "--refcalc"]
    elif list_only:
        args += ["--list"]
    else:
        args += ["--cases", cases]
        if verify:
            args.append("--verify")
        if force:
            args.append("--force")
    if out and (refcalc or not list_only):
        args += ["--out", str(out)]
    return _run_tool_script("build_golden.py", args)


# ─────────────────────────────────────────────────────────────
# 工具脚本委托（CLI 审查报告-20260930 P0/P2：门禁/冷备收编为子命令）
# ─────────────────────────────────────────────────────────────
def _run_tool_script(script_name: str, args: Sequence[str]) -> int:
    """子进程委托 `tools/<script_name>`（**单一真源**：CLI 只转发不做第二实现）。

    为什么不把判定逻辑搬进 `btf/`：门禁口径只能有一份——本项目文档长期写
    `bt check`，而它此前**不是** `bt` 子命令（文档与 CLI 脱节即是本函数要治
    的病）。委托保证 `bt check` 与 `python tools/check.py` 逐位同源，不存在
    "两条路各判一套"的漂移面。
    """
    script = Path(__file__).resolve().parents[2] / "tools" / script_name
    if not script.is_file():
        print(f"[error] 工具脚本缺失: {script}", file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, str(script), *args])


def run_gate_check() -> int:
    """`bt check`：九项架构门禁（委托 `tools/check.py`；退出码原样透传）。"""
    return _run_tool_script("check.py", [])


def cold_backup(
    *,
    reason: str | None = None,
    out: str | Path | None = None,
    verify: str | Path | None = None,
) -> int:
    """`bt cold-backup`：跨版本源码冷备（委托 `tools/cold_backup.py`）。

    `--verify DIR`（校验既有冷备）与"新建备份"互斥——脚本如此，故此处二选一；
    未给 `--reason` 时**不传**该参数（保留脚本默认值，避免 CLI 与脚本两套默认）。
    """
    args: list[str] = []
    if verify:
        args += ["--verify", str(verify)]
    else:
        if reason:
            args += ["--reason", str(reason)]
        if out:
            args += ["--out", str(out)]
    return _run_tool_script("cold_backup.py", args)


@dataclass(frozen=True)
class RunSummary:
    """`bt runs` 一行：run 目录摘要（只读；不改产物、不触发重算）。"""

    run_id: str
    status: str
    created: str                       # run 目录 mtime（本地时间）
    metrics: Mapping[str, float]
    note: str = ""                     # manifest/metrics 不可读时的原因


@dataclass(frozen=True)
class RunDetail:
    """`bt show --run <id>`：manifest + metrics 摘要（只读，不重算指标）。

    为什么不复用 `RunBundle`（`store.load`）：那会把 snapshots/fills 等
    全量 jsonl 读进内存（十年全市场 run 是百万行量级），而"看 manifest +
    指标"只需两个小文件——展示命令不应付读取全产物的代价。
    """

    run_id: str
    directory: Path
    manifest: Mapping[str, Any]
    metrics: Mapping[str, float]


def _dir_time(directory: Path) -> str:
    """run 目录 mtime → `YYYY-MM-DD HH:MM:SS`（不读 manifest：损坏目录也可列）。"""
    try:
        stamp = directory.stat().st_mtime
    except OSError:
        return "—"
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")


def list_runs(
    *,
    out_dir: str | Path | None = None,
    latest: int | None = None,
) -> list[RunSummary]:
    """列 RunStore 下的 run 摘要（按目录时间**新→旧**；`latest` 取前 N）。

    Store 经 `runtime.make_store` 取（铁律 5 / 新 10：入口层不直连 experiment）。
    manifest 缺失/损坏**不静默**：该行照列，`status` 退化为 `?`、`note` 写明
    原因——"列不出来"与"列出来但读不了"是两件事，后者必须可见（fail-visible）。
    """
    from btf.runtime import make_store

    store = make_store(out_dir)
    rows: list[RunSummary] = []
    for run_id in store.list_runs():
        directory = Path(store.run_dir(run_id))
        try:
            status, note = store.read_manifest(run_id).status, ""
        except (OSError, ValueError) as exc:       # 缺失/坏 JSON/契约不兼容
            status, note = "?", f"{type(exc).__name__}: {exc}"
        try:
            metrics: dict[str, float] = dict(store.read_metrics(run_id))
        except (OSError, ValueError) as exc:
            metrics = {}
            note = note or f"{type(exc).__name__}: {exc}"
        rows.append(RunSummary(run_id, status, _dir_time(directory), metrics,
                               note))
    rows.sort(key=lambda r: (r.created, r.run_id), reverse=True)
    return rows[:latest] if latest else rows


def run_detail(
    run_id: str,
    *,
    out_dir: str | Path | None = None,
) -> RunDetail:
    """读单个 run 的 manifest + metrics（只读；`run_id` 不存在 → FileNotFoundError）。"""
    from btf.runtime import make_store

    store = make_store(out_dir)
    manifest = store.read_manifest(run_id)
    return RunDetail(run_id=manifest.run_id, directory=Path(store.run_dir(run_id)),
                     manifest=manifest.to_dict(),
                     metrics=dict(store.read_metrics(run_id)))
