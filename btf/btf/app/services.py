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
from pathlib import Path
from typing import Any

__all__ = [
    "RunOutcome",
    "assemble_assumptions",
    "build_dataset",
    "build_report_file",
    "filter_btf_env",
    "parse_override_value",
    "parse_overrides",
    "run_backtest",
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
    out: str | Path | None = None,
) -> int:
    """黄金集构建（委托 `tools/build_golden.py` 子进程；CLI/api 同源）。"""
    script = Path(__file__).resolve().parents[2] / "tools" / "build_golden.py"
    if not script.is_file():
        print(f"[error] 构建脚本缺失: {script}", file=sys.stderr)
        return 2
    cmd = [sys.executable, str(script)]
    if list_only:
        cmd += ["--list"]
    else:
        cmd += ["--cases", cases]
        if verify:
            cmd.append("--verify")
        if force:
            cmd.append("--force")
        if out:
            cmd += ["--out", str(out)]
    return subprocess.call(cmd)
