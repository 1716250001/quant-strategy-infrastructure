# -*- coding: utf-8 -*-
"""btf.api —— 进程内门面（v0.5 V5-7；03 §7.3 铁律 5 / 新 10）。

**与 CLI 字节等价**的承诺（`api.run` ≡ `bt run`、`api.report` ≡ `bt report`、
`api.dataset` ≡ `bt dataset`）由**同一实现**保证：编排逻辑在 `btf.app`
（P2-3 / R4），本模块只做转发（铁律新 10：入口层不含编排）。
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from btf.app import services as _app


def run(
    config: str | Path | Mapping[str, Any],
    *,
    overrides: Mapping[str, Any] | None = None,
    out: str | Path | None = None,
    dry_run: bool = False,
) -> Any:
    """执行回测（镜像 `bt run`）：load_config → build → run（落盘）。

    - config：YAML 路径或已合并配置 dict（BTFRuntime.load_config 口径）；
    - overrides：L4 覆盖（同 `bt run --set` 的嵌套 dict 形态）；
    - out：RunStore 根（默认 BTF_OUTPUT_DIR/runs）；
    - dry_run：只装配不执行（返回 BTFRuntime——镜像 `--dry-run`）。

    返回 engine.RunResult（含 manifest / run_id / metrics）。
    """
    outcome = _app.run_backtest(config, overrides=overrides, out=out,
                               dry_run=dry_run)
    return outcome.runtime if dry_run else outcome.result


def report(
    run_id: str,
    *,
    out: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> Path:
    """由 RunStore 产物再生成 HTML 报告（镜像 `bt report`；幂等）。"""
    return _app.build_report_file(run_id, out=out, out_dir=out_dir)


def dataset(
    cases: str = "g1",
    *,
    action: str = "build-golden",
    verify: bool = False,
    force: bool = False,
    list_only: bool = False,
    out: str | Path | None = None,
) -> int:
    """黄金集构建（镜像 `bt dataset`——委托 tools/build_golden.py 子进程）。"""
    return _app.build_dataset(cases, action=action, verify=verify, force=force,
                              list_only=list_only, out=out)


__all__ = ["dataset", "report", "run"]
