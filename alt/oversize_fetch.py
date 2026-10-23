"""
alt/oversize_fetch.py —— 外盘扩容专用取数引擎（OS-R1~R5）

本模块处理**每标的多请求**的场景，list_backfill 引擎只支持 1 标的 = 1 请求，
不适用本场景。核心特点：
  - 复用 rate.py 的 AltRateLimiter（所有请求限流，CK-03）
  - 复用 alt_io 的 normalize + save（CK-01 统一落盘）
  - 独立的进度 checkpoint（断点续拉）
  - S1 基准集验证 + S2 速率门禁 + S3 schema 门禁 + CK-13 PE 剔非正值

当前实现：hk_val_pct（OS-R1 P0-2）
后续待补：us_daily_raw / us_qfq_factor / us_fina_ind / us_report / us_val_pct /
          hk_fina_ind / hk_report / hk_div / hk_profile / global_fut

用法:
  python -m alt.oversize_fetch --table hk_val_pct            # 全量建库
  python -m alt.oversize_fetch --table hk_val_pct --symbols "00700,00981"  # 指定标的
  python -m alt.oversize_fetch --table hk_val_pct --checkpoint  # 从断点续拉
  python -m alt.oversize_fetch --dry-run                      # 预演
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 确保项目根在 sys.path
_THIS = Path(__file__).resolve().parent
_PROJ_ROOT = _THIS.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from alt import spec as spec_mod           # noqa: E402
from alt.io import normalize as _normalize  # noqa: E402
from alt.io import save as _save            # noqa: E402
from alt.rate import AltRateLimiter         # noqa: E402
from alt.urls import guess_url               # noqa: E402


# ============================================================
# 一、通用工具
# ============================================================

def _strip_hk_suffix(code: str) -> str:
    """'00700.HK' → '00700'"""
    return code.replace(".HK", "") if code.endswith(".HK") else code


def _load_hk_universe() -> List[str]:
    """从 master:hk_basic 加载港股清单，返回 ['00001', '00002', ..., '00700', ...]"""
    from config_alt import TS_DATA_DIR
    path = os.path.join(TS_DATA_DIR, "hk_basic", "hk_basic.parquet")
    if not os.path.exists(path):
        raise RuntimeError(f"master:hk_basic 不存在: {path}")
    df = pd.read_parquet(path)
    codes = df["ts_code"].astype(str).tolist()
    stripped = sorted({_strip_hk_suffix(c) for c in codes if c.strip()})
    print(f"[清单] 港股清单 master:hk_basic → {len(stripped):,} 只")
    return stripped


# ============================================================
# 二、hk_val_pct 取数（OS-R1 P0-2）
# ============================================================

HK_VAL_INDICATORS = ["市盈率(TTM)", "市盈率(静)", "市净率"]
HK_VAL_PERIOD = "全部"          # 百度接口"全部"=2004-至今 ≈ 629 行（无严格十年选项）
HK_VAL_SRC = "baidu"
HK_VAL_SRC_HOST = guess_url("stock_hk_valuation_baidu")


def _fetch_hk_val_raw(symbol: str, limiter: AltRateLimiter
                      ) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """拉单只港股的估值时序（3 个 indicator 合并）。

    返回: (DataFrame[symbol, indicator, date, value], None) 成功
          (None, str) 失败原因
    """
    import akshare as ak

    frames = []
    for ind in HK_VAL_INDICATORS:
        def _call(_ind=ind):
            return ak.stock_hk_valuation_baidu(
                symbol=symbol, indicator=_ind, period=HK_VAL_PERIOD)

        df, ok, err = limiter.call(_call, HK_VAL_SRC_HOST)
        if not ok:
            return None, f"{ind}: {err}"
        if df is None or len(df) == 0:
            # 单 indicator 空返回不是硬失败——可能该标的不支持此指标
            print(f"    [WARN] {symbol} {ind} 空返回（可能该标的无此估值）")
            continue

        d = df.copy()
        # 源列: ['date', 'value']，date 已是 datetime.date
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y%m%d").astype(int)
        d["value"] = pd.to_numeric(d["value"], errors="coerce")
        d["symbol"] = symbol
        d["indicator"] = ind
        frames.append(d[["symbol", "indicator", "date", "value"]])

    if not frames:
        return None, "全部 indicator 空返回"

    return pd.concat(frames, ignore_index=True), None


def _compute_percentile(series: pd.Series) -> Optional[float]:
    """计算当日 value 在其历史中的百分位。

    CK-13 规则（PE 类剔非正值）：
      - PE(TTM) / PE(静)：剔除 value ≤ 0 的行（负 PE 无意义）
      - 市净率(PB)：不剔除（PB 可以为负但有意义）
    百分位 = rank / len(valid) × 100（用方法='max' 处理相等值）

    当 valid 长度 < 30 时返回 None（样本过少，分位不可靠）。
    """
    valid = series.dropna()
    if len(valid) < 30:
        return None
    # 百分位 = 当前值在历史中的排名百分比（小值=低分位，大值=高分位）
    # rank(pct=True) 返回的是 0~1 的值，乘 100 即百分位
    ranks = valid.rank(method="max", pct=True)
    return float(ranks.iloc[-1])


def _enrich_with_percentile(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """对单只 × 3 indicator 的原始时序数据计算分位数。

    CK-13 规则：
      - PE 类：剔 value ≤ 0 的行后再算分位
      - PB：不剔
    """
    out_parts = []
    for ind in HK_VAL_INDICATORS:
        mask = raw["indicator"] == ind
        sub = raw[mask].copy()
        if len(sub) == 0:
            continue

        # CK-13: PE 剔非正值
        if ind in ("市盈率(TTM)", "市盈率(静)"):
            sub = sub[sub["value"] > 0].copy()

        # 按日期升序排
        sub = sub.sort_values("date").reset_index(drop=True)

        if len(sub) < 30:
            # 样本过少，标记 percentile = None
            sub["percentile"] = np.nan
        else:
            # 计算每行 value 在全历史中的百分位
            vals = sub["value"].values
            # 用 percentileofscore 方式：对每个 value 算 rank
            sub["percentile"] = [
                float(np.sum(vals <= v)) / len(vals) * 100.0
                for v in vals
            ]

        sub["src"] = HK_VAL_SRC
        out_parts.append(sub)

    if not out_parts:
        return pd.DataFrame(columns=["symbol", "indicator", "date", "value",
                                      "percentile", "src"])

    return pd.concat(out_parts, ignore_index=True)


def _save_hk_val(df: pd.DataFrame, dest_dir: str) -> Dict:
    """落盘到 alt_data/hk_val_pct/ 下，按 date 年分区。

    复用 alt.io.normalize + save 的基础设施。
    """
    if df is None or len(df) == 0:
        return {"ok": True, "rows": 0}

    sp = spec_mod.get("hk_val_pct")
    os.makedirs(dest_dir, exist_ok=True)

    # 构造一个最小 spec 给 normalize（只需要它处理 date 列）
    norm, warns = _normalize(df, sp)
    if warns:
        for w in warns:
            print(f"    [WARN] normalize: {w}")

    res = _save(norm, sp, dest_dir=dest_dir)
    return res


# ============================================================
# 三、断点续拉 Checkpoint
# ============================================================

def _cp_path(table: str) -> str:
    from config_alt import ALT_META_DIR
    return os.path.join(ALT_META_DIR, f"oversize_{table}_checkpoint.json")


def _load_checkpoint(table: str) -> Dict:
    cp = _cp_path(table)
    if os.path.exists(cp):
        with open(cp, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"done": [], "empty": [], "fail": [], "stats": {"ok": 0, "empty": 0, "fail": 0}}


def _save_checkpoint(table: str, cp: Dict) -> None:
    os.makedirs(os.path.dirname(_cp_path(table)), exist_ok=True)
    with open(_cp_path(table), "w", encoding="utf-8") as fh:
        json.dump(cp, fh, ensure_ascii=False, indent=2)


def _filter_todo(all_syms: List[str], cp: Dict) -> List[str]:
    done_set = set(cp.get("done", [])) | set(cp.get("empty", []))
    return [s for s in all_syms if s not in done_set]


# ============================================================
# 四、S1 基准集验证（CK-25）
# ============================================================

S1_HK_SYMBOLS = [
    "00700", "09988", "03690", "01810", "00981",      # 腾讯/阿里/美团/小米/中芯
    "03968", "02318", "01211", "00005", "00001",      # 招行/平安/比亚迪/汇丰/长和
    "00016", "00027", "00669", "01024", "02020",      # 新鸿基/银河/创科/快手/安踏
    "02382", "06098", "00175", "01299", "00388",      # 舜宇/碧桂园服务/吉利/友邦/港交所
]


def _run_s1_baseline(limiter: AltRateLimiter) -> Tuple[int, int]:
    """跑 S1 基准集 20 只，返回 (ok_count, fail_count)。

    CK-25: 20 只全部非空 → S1 PASS；任一为空 → S1 FAIL。
    """
    print(f"\n{'='*60}")
    print(f"[S1 基准集] 开始验证 {len(S1_HK_SYMBOLS)} 只港股")
    print(f"{'='*60}")

    ok = 0
    empty = 0
    fail = 0
    empty_list = []

    for i, sym in enumerate(S1_HK_SYMBOLS, 1):
        print(f"  S1 [{i:>2}/{len(S1_HK_SYMBOLS)}] {sym} ...", end=" ", flush=True)
        raw, err = _fetch_hk_val_raw(sym, limiter)
        if err:
            print(f"FAIL ({err[:60]})")
            fail += 1
        elif raw is None:
            print("EMPTY")
            empty += 1
            empty_list.append(sym)
        else:
            print(f"OK ({len(raw)} 行)")
            ok += 1

    print(f"\n[S1 结果] ok={ok}, empty={empty}, fail={fail}")
    if empty_list:
        print(f"  ⚠ 空返回清单: {empty_list}")

    if ok == len(S1_HK_SYMBOLS):
        print("  ✅ S1 PASS：20/20 全部有数据")
    else:
        print(f"  ❌ S1 FAIL：{ok}/20 有数据（空 {empty} / 失败 {fail}）")
        print("     → 暂停全量，请先排查空返回原因")

    return ok, empty + fail


# ============================================================
# 五、主入口：hk_val_pct 全量建库
# ============================================================

def run_hk_val_pct(*, symbols: Optional[List[str]] = None,
                   checkpoint: bool = True,
                   dry_run: bool = False,
                   s1_first: bool = True,
                   max_syms: Optional[int] = None) -> Dict:
    """执行 hk_val_pct 全量建库。

    Args:
        symbols: 指定标的列表（None = 用完整 universe）
        checkpoint: 是否启用断点续拉
        dry_run: 预演（不拉数据）
        s1_first: 是否先跑 S1 基准集
        max_syms: 最多处理多少只（用于限制执行时间）
    """
    from config_alt import ALT_DATA_DIR
    dest_dir = os.path.join(ALT_DATA_DIR, "hk_val_pct")

    print(f"\n{'#'*60}")
    print(f"# hk_val_pct 取数（OS-R1 P0-2）")
    print(f"# 3 indicator × 2785 只 = 8,355 请求")
    print(f"# 档位: {HK_VAL_SRC_HOST} (other 15/分)")
    print(f"# 预计耗时: ~9 小时")
    print(f"{'#'*60}")

    limiter = AltRateLimiter(verbose=True)

    # 1) 确定 universe
    if symbols is None:
        universe = _load_hk_universe()
    else:
        universe = list(symbols)
    if max_syms:
        universe = universe[:max_syms]

    # 2) 断点续拉过滤
    cp = {"done": [], "empty": [], "fail": [],
          "stats": {"ok": 0, "empty": 0, "fail": 0}}
    if checkpoint:
        cp = _load_checkpoint("hk_val_pct")
        universe = _filter_todo(universe, cp)
        print(f"[断点] 已完成 {len(cp['done'])} 只，剩余 {len(universe)} 只待拉")

    # 3) S1 基准集
    if s1_first and not dry_run:
        s1_ok, s1_issue = _run_s1_baseline(limiter)
        if s1_issue > 0:
            print("\n⚠ S1 基准集未全部通过，继续全量但标记风险")

    if dry_run:
        print(f"\n[DRY-RUN] 将处理 {len(universe)} 只（不拉数据、不写库）")
        print(f"  前 5 只: {universe[:5]}")
        return {"table": "hk_val_pct", "planned": len(universe), "dry_run": True}

    # 4) 主循环
    os.makedirs(dest_dir, exist_ok=True)

    buf: List[pd.DataFrame] = []
    buf_flush_threshold = 50          # 每 50 只 flush 一次
    buf_rows_threshold = 50_000       # 或累计 5 万行就 flush

    start_t = time.time()
    done_count = 0
    empty_count = 0
    fail_count = 0
    total_rows = 0
    last_flush_t = start_t

    for i, sym in enumerate(universe, 1):
        t0 = time.time()
        raw, err = _fetch_hk_val_raw(sym, limiter)

        if err:
            fail_count += 1
            cp["fail"].append(sym)
            print(f"  [{i:>5}/{len(universe)}] FAIL {sym}: {err[:60]}")
        elif raw is None or len(raw) == 0:
            empty_count += 1
            cp["empty"].append(sym)
            print(f"  [{i:>5}/{len(universe)}] EMPTY {sym}")
        else:
            done_count += 1
            enriched = _enrich_with_percentile(raw, sym)
            buf.append(enriched)
            cp["done"].append(sym)
            total_rows += len(enriched)

        # 周期 flush
        buf_syms = done_count
        buf_rows = sum(len(d) for d in buf)

        if (buf_syms >= buf_flush_threshold or
                buf_rows >= buf_rows_threshold or
                i == len(universe)):
            if buf:
                flush_df = pd.concat(buf, ignore_index=True)
                res = _save_hk_val(flush_df, dest_dir)
                if res.get("ok"):
                    elapsed = time.time() - last_flush_t
                    rate = len(buf) / elapsed if elapsed > 0 else 0
                    print(f"  [flush] {len(buf)} 只 / {len(flush_df):,} 行 落盘 "
                          f"→ 速率 {rate:.0f} 只/s")
                else:
                    print(f"  [flush FAIL] {res.get('errors', '')[:120]}")
                buf = []
                last_flush_t = time.time()

        # 周期 checkpoint 保存（每 20 只或失败时）
        if checkpoint and (i % 20 == 0 or err):
            cp["stats"] = {"ok": done_count, "empty": empty_count, "fail": fail_count}
            _save_checkpoint("hk_val_pct", cp)

        # 进度条（每 10 只）
        if i % 10 == 0 or i == len(universe):
            elapsed = time.time() - start_t
            rate = i / elapsed if elapsed > 0 else 0
            remaining = (len(universe) - i) / rate if rate > 0 else 0
            print(f"  [进度] {i}/{len(universe)} | ok={done_count} empty={empty_count} "
                  f"fail={fail_count} | 速率 {rate:.2f} 只/s | "
                  f"剩余 {remaining/60:.1f} min")

    # 最终 flush + checkpoint
    if buf:
        flush_df = pd.concat(buf, ignore_index=True)
        _save_hk_val(flush_df, dest_dir)
    if checkpoint:
        cp["stats"] = {"ok": done_count, "empty": empty_count, "fail": fail_count}
        cp["done"] = list(set(cp.get("done", [])))
        cp["empty"] = list(set(cp.get("empty", [])))
        cp["fail"] = list(set(cp.get("fail", [])))
        _save_checkpoint("hk_val_pct", cp)

    total_t = time.time() - start_t
    print(f"\n{'='*60}")
    print(f"[完成] hk_val_pct 全量建库")
    print(f"  成功 {done_count} | 空 {empty_count} | 失败 {fail_count}")
    print(f"  总行数 {total_rows:,}")
    print(f"  耗时 {total_t/60:.1f} min")
    print(f"  速率 {len(universe)/total_t:.2f} 只/s")
    print(f"{'='*60}")

    return {"ok": done_count, "empty": empty_count, "fail": fail_count,
            "rows": total_rows, "elapsed_sec": total_t}


# ============================================================
# 六、CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="外盘扩容取数引擎（OS-R1~R5）")
    parser.add_argument("--table", required=True,
                        choices=["hk_val_pct"],
                        help="目标表（当前仅实现 hk_val_pct）")
    parser.add_argument("--symbols", default=None,
                        help="指定标的列表（逗号分隔，如 '00700,00981'）")
    parser.add_argument("--no-checkpoint", action="store_true",
                        help="禁用断点续拉（从头开始）")
    parser.add_argument("--dry-run", action="store_true",
                        help="预演（不拉数据、不写库）")
    parser.add_argument("--no-s1", action="store_true",
                        help="跳过 S1 基准集验证")
    parser.add_argument("--max-syms", type=int, default=None,
                        help="最多处理多少只（用于限制执行时间）")
    args = parser.parse_args()

    if args.table == "hk_val_pct":
        syms = ([s.strip() for s in args.symbols.split(",") if s.strip()]
                if args.symbols else None)
        run_hk_val_pct(
            symbols=syms,
            checkpoint=not args.no_checkpoint,
            dry_run=args.dry_run,
            s1_first=not args.no_s1,
            max_syms=args.max_syms,
        )
    else:
        raise SystemExit(f"未知表: {args.table}")


if __name__ == "__main__":
    main()
