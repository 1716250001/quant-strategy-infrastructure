"""
alt/oversize_fetch.py —— 外盘扩容专用取数引擎（OS-R1~R5）

本模块处理**每标的多请求**的场景，list_backfill 引擎只支持 1 标的 = 1 请求，
不适用本场景。核心特点：
  - 复用 rate.py 的 AltRateLimiter（所有请求限流，CK-03）
  - 复用 alt_io 的 normalize + save（CK-01 统一落盘）
  - 独立的进度 checkpoint（断点续拉）
  - S1 基准集验证 + S2 速率门禁 + S3 schema 门禁 + CK-13 PE 剔非正值

当前实现: hk_val_pct（专用多请求引擎）
其余表（us_qfq_factor / us_daily_raw / us_fina_ind / us_report /
        hk_fina_snap / hk_fina_ind / hk_report）→ 路由到 list_backfill（list 模式，1 标的=1 请求）

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

# ============================================================
# SSL_CERT_FILE 自举（§113 RE1-3 / D3）
# ------------------------------------------------------------
# 为什么必须在这里做：`verify_os_r1r.py` 早已自举，但**生产入口没有**——
# 造成"验证脚本能跑通、生产入口不设环境变量就全崩（curl 77 SSLError）"。
# §111 审查方代跑 R-1 时正是踩了这个坑（M1：漏设 → S1 全量 SSLError 白跑）。
# 规则：**不覆盖用户已设值**；探测顺序与 verify_os_r1r.py 保持一致。
# ============================================================
if not os.environ.get("SSL_CERT_FILE"):
    _ssl_candidates = [r"D:\yf_ca\cacert.pem"]
    try:
        import certifi as _certifi
        _ssl_candidates.append(_certifi.where())
    except ImportError:
        pass
    _ssl_candidates.extend([
        str(Path(sys.prefix) / "Library" / "ssl" / "cacert.pem"),
        str(Path(sys.prefix) / "cacert.pem"),
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
    ])
    for _c in _ssl_candidates:
        if os.path.exists(_c):
            os.environ["SSL_CERT_FILE"] = _c
            print(f"[bootstrap] SSL_CERT_FILE = {_c}")
            break
    else:
        print("[bootstrap] ⚠ 未找到 SSL 证书；akshare 网络调用可能失败")

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
HK_VAL_PERIOD = "近十年"           # 百度接口 period：近十年=731点/2016-10-25起；全部=629点/2004起
HK_VAL_SRC = "baidu"
HK_VAL_SRC_HOST = guess_url("stock_hk_valuation_baidu")

# 指标级重试（§113 RE1-2a）：单指标失败后再试 2 次，退避 n × 2s（2s / 4s）
INDICATOR_RETRY = 2
INDICATOR_BACKOFF_SEC = 2.0


def _fetch_hk_val_raw(symbol: str, limiter: AltRateLimiter
                      ) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """拉单只港股的估值时序（3 个 indicator 合并）。

    返回: (DataFrame[symbol, indicator, date, value], None) 成功
          (None, str) 失败原因

    §113 RE1-2（D2）修复：**指标级容错**
      原实现 `if not ok: return None` —— 3 个 indicator 里只要有 1 个瞬态失败，
      整只标的就作废（已成功的 2 个指标数据一起丢掉，S4「部分成功」未落地）。
      现改为：
        a) 单指标失败 → **重试 2 次**（退避 2s / 4s）；
        b) 重试仍失败 → 记入 `indicator_fails`，**continue 到下一指标**，不中止整只；
        c) 只要**至少 1 个指标**取到数据 → 返回该数据，失败清单写入 `df.attrs["indicator_fails"]`；
        d) **3 个指标全败**（或全部空返回）才返回 `(None, ...)`。
    """
    import akshare as ak

    frames = []
    indicator_fails = []

    for ind in HK_VAL_INDICATORS:
        def _call(_ind=ind):
            return ak.stock_hk_valuation_baidu(
                symbol=symbol, indicator=_ind, period=HK_VAL_PERIOD)

        df, ok, err = limiter.call(_call, HK_VAL_SRC_HOST)

        # (a) 指标级重试：退避 2s / 4s
        retries = 0
        while not ok and retries < INDICATOR_RETRY:
            retries += 1
            time.sleep(INDICATOR_BACKOFF_SEC * retries)
            df, ok, err = limiter.call(_call, HK_VAL_SRC_HOST)

        if not ok:
            # (b) 记失败指标后继续，不中止整只
            indicator_fails.append(ind)
            print(f"    [WARN] {symbol} {ind} 失败（重试 {retries} 次后仍失败）: "
                  f"{str(err)[:60]}")
            continue

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
        # (d) 全败才整体失败
        if indicator_fails:
            return None, f"全部 indicator 失败: {indicator_fails}"
        return None, "全部 indicator 空返回"

    out = pd.concat(frames, ignore_index=True)
    # (c) 失败指标清单随 DataFrame 返回（不改变返回签名，避免动调用方）
    out.attrs["indicator_fails"] = indicator_fails
    return out, None


def _enrich_with_percentile(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """对单只 × 3 indicator 的原始时序数据计算分位数。

    CK-13 规则：
      - PE 类：剔 value ≤ 0 的行后再算分位
      - PB：不剔

    CK-14 三道门禁（PE 类，派单硬性要求）：
      1. cur > 0（当前值须正）
      2. n_pos >= 100（正样本数 ≥ 100）
      3. n_total >= 520（近十年窗口 ≈ 731 点，允许有部分负值）
    三道全过才给 PE 分位；不满足时 PE 分位置空 → 转 PB 分位。

    R1R-3（消除 look-ahead）：percentile 只存**当前值**一行（当日分位），
    不在历史行上存分位（历史分位若存就会 look-ahead）。
    表级结构：date = 拉取当日（inject_const 注入），percentile = 该日当前值分位。
    历史 value 序列仅作分位计算的输入窗口，不逐行存分位。
    """
    out_parts = []
    for ind in HK_VAL_INDICATORS:
        mask = raw["indicator"] == ind
        sub = raw[mask].copy()
        if len(sub) == 0:
            continue

        # 按日期升序排
        sub = sub.sort_values("date").reset_index(drop=True)

        # CK-13: PE 剔非正值（留原始序列用于 CK-14 判 n_total）
        n_total = len(sub)                                       # 窗口总点数
        if ind in ("市盈率(TTM)", "市盈率(静)"):
            valid = sub[sub["value"] > 0].copy()                  # 剔非正值
        else:
            valid = sub.copy()                                    # PB 不剔

        n_pos = len(valid)

        # CK-14 三道门禁：cur_val 必须从【原始未过滤】sub 取，
        # 因为 CK-13 已经把 value ≤ 0 从 valid 里剔掉了，
        # 从 valid 取 cur_val 会导致 gate1(cur>0) 永真——
        # 审查方实测全港 51.1% 标的 PE 当前值为负，这些必须被 gate1 拦住。
        cur_val = float(sub["value"].iloc[-1])                    # 当前值（原始序列末尾）
        cur_date = int(sub["date"].iloc[-1])                      # 当前日期（原始序列末尾）

        # CK-14 PE 类三道门禁
        pct_value = np.nan
        if ind in ("市盈率(TTM)", "市盈率(静)"):
            gate1 = cur_val > 0                                   # 门 1：cur > 0（原始值，非过滤后）
            gate2 = n_pos >= 100                                  # 门 2：正样本数 ≥ 100
            gate3 = n_total >= 520                                # 门 3：窗口总点数 ≥ 520
            if gate1 and gate2 and gate3 and n_pos > 0:
                # 用 CK-13 过滤后的 valid 序列算分位（符合派单 CK-13 口径）
                pct_value = float(np.sum(valid["value"].values <= cur_val)) / n_pos * 100.0
            # 任一门禁未过 → PE 分位置空（下游转 PB 分位）
        else:
            # PB：CK-14 不适用于 PB（派单 CK-14 仅约束 PE 类）
            # 但 n_pos 太少仍不可靠
            if n_pos >= 30:
                pct_value = float(np.sum(valid["value"].values <= cur_val)) / n_pos * 100.0

        # R1R-3: 只存当前值一行（消除 look-ahead）
        # date = "近十年" 窗口最新一天
        row = pd.DataFrame([{
            "symbol": symbol,
            "indicator": ind,
            "date": cur_date,                                      # 窗口最新日
            "value": cur_val,                                      # 当前值（可能负/零）
            "percentile": pct_value,                                # 当前值分位（可能 NaN）
            "src": HK_VAL_SRC,
        }])
        out_parts.append(row)

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
            print("\n❌ S1 基准集未全部通过 → 退出（纪律要求）")
            sys.exit(1)

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
            enriched = _enrich_with_percentile(raw, sym)
            buf.append((sym, enriched))                    # 暂存，不记 done

        # 周期 flush
        if (len(buf) >= buf_flush_threshold or
                sum(len(d) for _, d in buf) >= buf_rows_threshold or
                (i == len(universe) and buf)):
            flush_df = pd.concat([d for _, d in buf], ignore_index=True)
            res = _save_hk_val(flush_df, dest_dir)
            if res.get("ok"):
                # B1 修复：落盘成功才记 done
                for sym_ok, _ in buf:
                    cp["done"].append(sym_ok)
                    done_count += 1
                elapsed = time.time() - last_flush_t
                rate = len(buf) / elapsed if elapsed > 0 else 0
                total_rows += len(flush_df)
                print(f"  [flush OK] {len(buf)} 只 / {len(flush_df):,} 行 "
                      f"→ 速率 {rate:.0f} 只/s")
                buf = []
                last_flush_t = time.time()
            else:
                # B1 修复：flush 失败 → 保留 buf 不清空，记 fail
                for sym_bad, _ in buf:
                    cp["fail"].append(sym_bad)
                    fail_count += 1
                print(f"  [flush FAIL] {res.get('errors', '')[:120]} → "
                      f"保留 {len(buf)} 只待下次重试")

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
                        choices=["hk_val_pct", "us_qfq_factor", "us_daily_raw",
                                 "hk_fina_snap", "us_fina_ind", "us_report",
                                 "hk_fina_ind", "hk_report"],
                        help="目标表")
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
    # §128 T1-2 / T2：list 表的轮次预算与分段停机点（原样转发给 list_backfill.run）
    parser.add_argument("--budget-syms", type=int, default=None, dest="budget",
                        help="list 表：本轮最多拉 N 只（首建传清单总数，如 18052）")
    parser.add_argument("--until", default=None,
                        help="分段停机点 HH:MM（到点不再发起新批次，在途批次完成并落盘后退出 0）")
    # §130 RE3-5：主入口必须能显式跳过交互确认（原 list_backfill 有、本入口未透传）
    parser.add_argument("--yes", action="store_true", help="跳过交互确认（无人值守必带）")
    args = parser.parse_args()

    if args.table == "hk_val_pct":
        if args.budget or args.until:
            print("[提示] hk_val_pct 走独立引擎（oversize_fetch.run_hk_val_pct），"
                  f"不支持 --budget-syms/--until（已忽略: budget={args.budget}, until={args.until}）")
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
        # 其余表走 list_backfill 引擎（list 模式表，1 标的 = 1 请求）
        from alt.list_backfill import run as list_run
        sym_str = args.symbols  # 已是 "AAPL,MSFT" 格式
        list_run(
            args.table,
            symbols=sym_str,
            dry_run=args.dry_run,
            budget=args.budget,
            until=args.until,
            yes=args.yes,
        )


if __name__ == "__main__":
    main()
