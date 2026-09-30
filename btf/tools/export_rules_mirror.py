# -*- coding: utf-8 -*-
"""规则镜像导出：md_core 镜像区 → rules_mirror_v77.json（M3 任务 6.1）。

    python tools/export_rules_mirror.py [--out 赤潮/rules_mirror_v77.json]
                                        [--mdcore-root d:/量化策略/赤潮]

H2 单一真源链路（04 §8.3.7）：
    规则源 赤潮/rules/single-source.md（v7.7，权威·人读）
      → 代码镜像 赤潮/md_core/paths.py（V75_DEFAULTS / LIQ_THRESHOLDS）
      → **本脚本导出** rules_mirror_v77.json（btf 侧规则缓存）
      → MirrorJsonRulesProvider 读取 → rules_version 进 manifest

纪律：
    - **只读** md_core（不修改现有工程文件，18 号第七节集成约定）；
    - 单位口径**以 md_core 镜像区为准**（roe_min/dv_min 为**百分数**），
      并在 JSON 的 `units` 中显式声明（04 文档侧小数口径经 `aliases` 映射）；
    - 指纹 `rules_version = "v7.7@sha256:<规则内容哈希>"`——规则升级必变；
    - 幂等：同镜像区内容 → 同 JSON（内容哈希不含导出时间）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

#: 单位口径（md_core 镜像区口径：比率 vs 百分数）
UNITS: dict[str, str] = {
    "pe_max": "ratio", "pb_max": "ratio",
    "roe_min": "percent", "dv_min": "percent",
    "sh_crisis": "percent", "sh_watch": "percent",
    "small_crisis": "percent", "small_watch": "percent",
    "down_crisis": "count", "down_watch": "count",
    "ratio_crisis": "percent", "ratio_watch": "percent",
}

#: 文档键名（04 §8.3.7）→ 镜像键名（md_core）
ALIASES: dict[str, str] = {
    "pe_ttm_max": "pe_max",
    "roe_waa_min": "roe_min",
    "L2_filter": "L2_filter",
}

SCHEMA_VERSION = "rules_mirror.v1"


def digest_of(rules: Mapping[str, Mapping[str, Any]]) -> str:
    """规则内容哈希（确定性：键排序 + 紧凑 JSON）。"""
    text = json.dumps({k: dict(v) for k, v in sorted(rules.items())},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_payload(v75: Mapping[str, Any], liq: Mapping[str, Any], *,
                  version: str = "v7.7",
                  source_file: str = "赤潮/rules/single-source.md",
                  mirror_file: str = "赤潮/md_core/paths.py",
                  exported_at: str | None = None) -> dict[str, Any]:
    """镜像常量 → 镜像 JSON 载荷（纯函数，可单测）。"""
    rules = {
        "L2_filter": {k: v75[k] for k in sorted(v75)},
        "L0_LIQ": {k: liq[k] for k in sorted(liq)},
    }
    digest = digest_of(rules)
    return {
        "schema_version": SCHEMA_VERSION,
        "rules_version": f"{version}@sha256:{digest}",
        "source": {"file": source_file, "version": version},
        "mirror": {"file": mirror_file},
        "units": {k: UNITS.get(k, "") for k in sorted(rules["L0_LIQ"] | rules["L2_filter"])},
        "aliases": dict(ALIASES),
        "rules": rules,
        "exported_at": exported_at,          # None = 幂等（不写时间戳）
    }


def load_from_mdcore(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """只读加载 md_core 镜像区两个常量（沿用 tools/align_mdcore.py 套路）。"""
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from md_core import paths

    return dict(paths.V75_DEFAULTS), dict(paths.LIQ_THRESHOLDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="规则镜像导出（H2 单一真源）")
    parser.add_argument("--out", default=r"D:\量化策略\赤潮\rules_mirror_v77.json",
                        help="输出 JSON 路径")
    parser.add_argument("--mdcore-root", default=r"d:\量化策略\赤潮",
                        help="md_core 所在根目录")
    parser.add_argument("--version", default="v7.7", help="规则源版本标识")
    parser.add_argument("--stamp", action="store_true",
                        help="写入 exported_at（破坏幂等，仅审计用）")
    parser.add_argument("--print", dest="show", action="store_true",
                        help="只打印不落盘")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        v75, liq = load_from_mdcore(Path(args.mdcore_root))
    except ImportError as exc:
        print(f"[error] md_core 不可用：{exc}", file=sys.stderr)
        return 2

    payload = build_payload(
        v75, liq, version=args.version,
        exported_at=datetime.now().isoformat(timespec="seconds")
        if args.stamp else None)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.show:
        print(text)
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"[ok] 规则镜像导出: {out}")
    print(f"     rules_version: {payload['rules_version']}")
    print(f"     L2_filter: {payload['rules']['L2_filter']}")
    print(f"     L0_LIQ   : {payload['rules']['L0_LIQ']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
