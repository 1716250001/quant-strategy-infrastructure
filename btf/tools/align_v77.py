# -*- coding: utf-8 -*-
"""v7.7 对账：btf 选股/LIQ 状态 vs md_core（M3 任务 6.2 验收 A1/A2）。

    python tools/align_v77.py --date 20260923
                              [--root d:/量化策略/赤潮]
                              [--json D:/量化策略/赤潮/rules_mirror_v77.json]
                              [--skip-mdcore]     # 只跑 btf 侧（无 md_core 环境）

A1 选股一致：btf `screen_l2`（PIT 口径）与 md_core `screen_stocks_v75`
    同日、同参数名单一致率（口径差异见下）；
A2 状态一致：btf `state_of`（L0-LIQ）与 md_core `get_l0_liq_state` 同日
    status 一致（NORMAL/WATCH/CRISIS）。

**已知口径差异（显式披露，非静默）**：
    - ROE PIT：md_core 用"缓存内最新年报"（建库视角，历史日含前视），
      btf 用 `ann_date ≤ 回测日`（真 PIT）——**同日（今天）应一致**，
      历史回测日可能分化；对账以"当日"为准并打印差异明细；
    - 跌停分母：md_core 取 stk_limit ∩ daily（down_limit>0），btf 同口径；
      容差 md_core 1e-6 / btf liq 1e-6（一致）。

退出码：0 = 全一致；1 = 有差异；3 = 环境缺失（md_core/主库/镜像 JSON）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent      # 代码/btf
if str(_PROJECT_ROOT) not in sys.path:                      # 脚本自举（btf 包）
    sys.path.insert(0, str(_PROJECT_ROOT))

EXIT_OK, EXIT_DIFF, EXIT_ERROR = 0, 1, 3

DEFAULT_ROOT = r"d:\量化策略\赤潮"
DEFAULT_JSON = r"D:\量化策略\赤潮\rules_mirror_v77.json"


def _btf_screen(date_ymd: str, params: dict) -> list[str]:
    from btf.data.fundamentals import screen_l2

    return screen_l2(date_ymd, params)


def _btf_liq(date_ymd: str, thresholds: dict):
    from btf.data.liq import state_of

    return state_of(date_ymd, thresholds)


def _mdcore(root: Path):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import md_core as md

    return md


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v7.7 对账（btf vs md_core）")
    parser.add_argument("--date", required=True, help="对账交易日 YYYYMMDD")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="赤潮根目录")
    parser.add_argument("--json", default=DEFAULT_JSON, help="规则镜像 JSON")
    parser.add_argument("--skip-mdcore", action="store_true",
                        help="只跑 btf 侧（md_core 不可用/避免联网）")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    json_path = Path(args.json)
    if not json_path.is_file():
        print(f"[error] 规则镜像缺失: {json_path}", file=sys.stderr)
        return EXIT_ERROR
    import json

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    rules = payload.get("rules") or {}
    l2_params, liq_th = rules.get("L2_filter") or {}, rules.get("L0_LIQ") or {}
    if not l2_params or not liq_th:
        print("[error] 镜像 JSON 缺 L2_filter / L0_LIQ", file=sys.stderr)
        return EXIT_ERROR
    print(f"rules_version: {payload.get('rules_version')}")
    print(f"L2_filter: {l2_params}")

    diffs: list[str] = []

    # ── A1 选股 ──
    btf_picks = _btf_screen(args.date, l2_params)
    print(f"A1 btf 选股: {len(btf_picks)} 只")
    if not args.skip_mdcore:
        try:
            _mdcore(Path(args.root))                 # 确保 md_core 可导入
            from md_core.screening import screen_stocks_v75
            table = screen_stocks_v75(
                trade_date=args.date, pe_max=l2_params["pe_max"],
                pb_max=l2_params["pb_max"], roe_min=l2_params["roe_min"],
                dv_min=l2_params["dv_min"])
            md_picks = sorted(table["ts_code"].tolist())
        except Exception as exc:                     # md_core 环境/数据异常
            print(f"[error] md_core 选股失败：{exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"A1 md_core 选股: {len(md_picks)} 只")
        only_btf = sorted(set(btf_picks) - set(md_picks))
        only_md = sorted(set(md_picks) - set(btf_picks))
        if only_btf or only_md:
            diffs.append(
                f"A1 名单差异：仅 btf {len(only_btf)} 只{only_btf[:5]}；"
                f"仅 md_core {len(only_md)} 只{only_md[:5]}")
        else:
            print(f"A1 [PASS] 名单一致（{len(btf_picks)} 只）")

    # ── A2 L0-LIQ 状态 ──
    state = _btf_liq(args.date, liq_th)
    print(f"A2 btf 状态: {state.status}（上证 {state.sh_pct}% / "
          f"小盘 {state.small_pct}% / 跌停 {state.down_cnt} 家 "
          f"{state.down_ratio:.2f}%）")
    if not args.skip_mdcore:
        try:
            _mdcore(Path(args.root))                 # 确保 md_core 可导入
            from md_core.market_state import get_l0_liq_state
            md_status = get_l0_liq_state(trade_date=args.date).get("status")
        except Exception as exc:
            print(f"[error] md_core 状态失败：{exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"A2 md_core 状态: {md_status}")
        if md_status != state.status:
            diffs.append(f"A2 状态不一致：btf={state.status} md_core={md_status}")
        else:
            print("A2 [PASS] 状态一致")

    if diffs:
        print(f"[FAIL] 对账差异 {len(diffs)} 项")
        for item in diffs:
            print(f"   - {item}")
        return EXIT_DIFF
    print("[PASS] v7.7 对账通过（A1/A2）")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
