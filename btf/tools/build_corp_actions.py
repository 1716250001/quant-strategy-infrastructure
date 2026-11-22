# -*- coding: utf-8 -*-
"""公司行动探针**写盘入口**（G3/D7：从运行时包移到 tools/）。

    python tools/build_corp_actions.py --out <dir> --snapshot <akshare stock_pg_em CSV>
    python tools/build_corp_actions.py --out <dir> --snapshot <csv> --years 1991-2026

只做 CLI 与落盘；检测/校验逻辑仍在 `btf.data.corp_action_probe`（运行时只读）。
产出：`corp_actions.json` + `consolidations_confirmation.json`（离线清单）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TOOLS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from btf.config.paths import CORP_ACTION_DIR, MARKET_DATA_DIR
from btf.data.corp_action_probe import build_artifact


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="公司行动探针（R1′/R2′/R6′）")
    ap.add_argument("--out", default=str(CORP_ACTION_DIR), help="清单输出目录")
    ap.add_argument("--root", default=str(MARKET_DATA_DIR), help="主库根")
    ap.add_argument("--snapshot", default=None, help="akshare 配股快照 CSV")
    ap.add_argument("--years", default=None, help="扫描年区间，如 1991-2026")
    args = ap.parse_args(argv)
    if not args.snapshot:
        print("[error] 需 --snapshot <akshare stock_pg_em CSV>（离线快照）")
        return 2
    years = None
    if args.years:
        y0, y1 = args.years.split("-")
        years = [int(y0), int(y1)]
    data = build_artifact(args.root, args.snapshot, years=years)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "corp_actions.json"
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # **R18′-a**：确证扫描全报告归档（confirmed/rejected 全量，可复现）
    (out / "consolidations_confirmation.json").write_text(
        json.dumps(
            data.get("consolidation_confirmation_report") or {},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    st = data["stats"]
    print(f"[ok] 清单落盘：{path}")
    print(
        f"     跳变总 {st['jumps_total']}（向上 {st['up_total']} / 向下 "
        f"{st['down_total']}）｜分红解释：向上 {st['up_explained_by_dividend']}"
        f" / 向下 {st['down_explained_by_dividend']}"
    )
    print(
        f"     配股候选 {st['up_candidates']} ⇒ 校验通过 "
        f"{st['rights_validated']} / 挂起 {st['rights_suspended']} / 无快照匹配 "
        f"{st['rights_unmatched']}；缩股候选 {st['consolidation_candidates']}"
    )
    cr = data.get("consolidation_confirmation_report") or {}
    cst = cr.get("stats") or {}
    print(
        f"     缩股确证（股本级，{cst.get('years')}）：股本减少 "
        f"{cst.get('share_drops')} / 因子跳变(去重) {cst.get('adj_jumps_dedup')}"
        f" ⇒ 确证 {cst.get('confirmed')}（{[c['symbol'] + ' ' + c['ex_date'] for c in cr.get('confirmed') or []]}）"
        f" / 排除 {cst.get('rejected')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
