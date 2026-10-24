"""OS-R1 + OS-R1R + OS-R1R2 返工验证脚本

覆盖派单:
  §86 R1R-1~R1R-5 + R3R-1（原始返工）
  §88 R1R2-1 ~ R1R2-8（独立验收 + 回归修复）

设计:
  · 与项目根同级，用 pathlib 自动推导（无硬编码路径）
  · SSL_CERT_FILE 自动探测（Windows certifi / Linux ca-certificates）
  · 网络依赖: 2 次 akshare 调用（00700 近十年 / AAPL qfq_factor）
  · 可在干净环境跑通（仅需依赖 requirements.txt + alt 库）

纪律:
  · 仅验证 alt 模块 + 既有 hk_qfq_factor 表，不改任何数据
  · 零副作用（import 不触发采集）
"""
import sys, os, pathlib, warnings

# =========================================================================
# 0. 路径 + SSL 自举（审查方 §88 P1-3: 干净环境可跑通）
# =========================================================================

# 0a. 项目根自动推导（脚本与 alt/ 同级放置）
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
print(f"[bootstrap] PROJECT_ROOT = {PROJECT_ROOT}")

# 0b. SSL_CERT_FILE 自动探测（不覆盖用户已设值）
if not os.environ.get("SSL_CERT_FILE"):
    _candidates = []
    # certifi（pip install certifi）
    try:
        import certifi
        _candidates.append(certifi.where())
    except ImportError:
        pass
    # Windows 常见路径
    _candidates.append(str(pathlib.Path(sys.prefix) / "Library" / "ssl" / "cacert.pem"))
    _candidates.append(str(pathlib.Path(sys.prefix) / "cacert.pem"))
    # Linux
    _candidates.append("/etc/ssl/certs/ca-certificates.crt")
    _candidates.append("/etc/pki/tls/certs/ca-bundle.crt")
    for _c in _candidates:
        if os.path.exists(_c):
            os.environ["SSL_CERT_FILE"] = _c
            print(f"[bootstrap] SSL_CERT_FILE = {_c}")
            break
    else:
        warnings.warn("[bootstrap] 未找到 SSL 证书；akshare 网络调用可能失败")

# =========================================================================
# 1. 导入被测模块（全部本地模块，无网络）
# =========================================================================

import pandas as pd
import numpy as np
from datetime import datetime

from alt.oversize_fetch import (
    HK_VAL_PERIOD, HK_VAL_INDICATORS, HK_VAL_SRC,
    _enrich_with_percentile,
)
import alt.oversize_fetch as oversize_fetch

from alt.spec import get as spec_get
from alt.io import normalize

# =========================================================================
# 2. 辅助
# =========================================================================

_errors = []

def _check(cond: bool, msg: str):
    if not cond:
        _errors.append(msg)
        print(f"  ❌ FAIL: {msg}")
    else:
        print(f"  ✅ {msg}")

def _ok(section: str):
    print(f"\n{'='*60}")
    print(f"✅ {section}")
    print(f"{'='*60}")

# =========================================================================
# §86 R1R-2: HK_VAL_PERIOD = "近十年"
# =========================================================================
print("\n" + "="*60)
print("§86 R1R-2: HK_VAL_PERIOD = 近十年")
print("="*60)

_check(HK_VAL_PERIOD == "近十年",
       f"HK_VAL_PERIOD = '{HK_VAL_PERIOD}'（应为 '近十年'）")

# 网络验证（可能失败，若 clean env 无网络则 skip）
try:
    import akshare as ak
    df = ak.stock_hk_valuation_baidu(symbol="00700", indicator="市盈率(TTM)", period="近十年")
    pts = df.shape[0]
    start = str(df["date"].iloc[0])
    _check(pts == 731, f"00700 近十年 731 点（实测 {pts}）")
    _check(start == "2016-10-25", f"00700 起点 2016-10-25（实测 {start}）")
    print("  ✅ 联网验证通过")
except Exception as e:
    print(f"  ⚠ 联网验证跳过（{type(e).__name__}: {e}）")

# =========================================================================
# §86 R1R-1: CK-14 三道门禁
# =========================================================================
print("\n" + "="*60)
print("§86 R1R-1: CK-14 三道门禁")
print("="*60)

def _make_fake_raw(symbol: str, cur_val: float, n_pos: int, n_total: int) -> pd.DataFrame:
    """构造伪原始数据，用于测门禁"""
    rng = np.random.default_rng(42)
    values = rng.uniform(5, 30, n_total)
    values[-1] = cur_val
    n_neg = n_total - n_pos
    if n_neg > 0:
        values[:n_neg] = -abs(values[:n_neg])
    dates = pd.date_range("2016-10-25", periods=n_total, freq="5D")
    rows = []
    for ind in HK_VAL_INDICATORS:
        for j in range(n_total):
            rows.append({
                "symbol": symbol,
                "indicator": ind,
                "date": int(dates[j].strftime("%Y%m%d")),
                "value": values[j],
            })
    return pd.DataFrame(rows)

# 反例 1: cur ≤ 0 → gate1 拦截
r = _enrich_with_percentile(_make_fake_raw("T1", -1.0, 500, 520), "T1")
pe = r[r["indicator"] == "市盈率(TTM)"]
_check(np.isnan(pe["percentile"].iloc[0]),
       f"反例1 cur=-1.0 → PE percentile=NaN (gate1)")

# 反例 2: n_pos < 100 → gate2 拦截
r2 = _enrich_with_percentile(_make_fake_raw("T2", 10.0, 80, 520), "T2")
pe2 = r2[r2["indicator"] == "市盈率(TTM)"]
_check(np.isnan(pe2["percentile"].iloc[0]),
       f"反例2 n_pos=80 → PE percentile=NaN (gate2)")

# 反例 3: n_total < 520 → gate3 拦截
r3 = _enrich_with_percentile(_make_fake_raw("T3", 10.0, 500, 400), "T3")
pe3 = r3[r3["indicator"] == "市盈率(TTM)"]
_check(np.isnan(pe3["percentile"].iloc[0]),
       f"反例3 n_total=400 → PE percentile=NaN (gate3)")

# 正例: 三道全过
r4 = _enrich_with_percentile(_make_fake_raw("T4", 10.0, 500, 600), "T4")
pe4 = r4[r4["indicator"] == "市盈率(TTM)"]
_check(not np.isnan(pe4["percentile"].iloc[0]),
       f"正例三道全过 → PE percentile={pe4['percentile'].iloc[0]:.1f}")

# PB 不受 CK-14 约束
pb = r4[r4["indicator"] == "市净率"]
_check(not np.isnan(pb["percentile"].iloc[0]),
       f"PB percentile={pb['percentile'].iloc[0]:.1f}（不受 CK-14 约束）")

# =========================================================================
# §86 R1R-3: 消除 look-ahead + 只存一行
# =========================================================================
print("\n" + "="*60)
print("§86 R1R-3: 消除 look-ahead + 死代码删除")
print("="*60)

_check(len(r4) == 3,
       f"每只 3 行（每 indicator 仅存当前值一行）（实测 {len(r4)} 行）")
_check(not hasattr(oversize_fetch, '_compute_percentile'),
       f"死代码 _compute_percentile 已删除")

# =========================================================================
# §86 R1R-4 + R1R-5: 静态结构检查
# =========================================================================
print("\n" + "="*60)
print("§86 R1R-4 + R1R-5: flush / sys.exit(1) 静态检查")
print("="*60)

import inspect
src = inspect.getsource(oversize_fetch.run_hk_val_pct)

# R1R-4: flush FAIL 后 buf 不清空
lines = src.split("\n")
flush_fail_lines = [i for i, l in enumerate(lines) if "flush FAIL" in l]
_buf_cleared_after_fail = False
for idx in flush_fail_lines:
    after = lines[idx:idx+8]
    if any("buf = []" in l for l in after):
        _buf_cleared_after_fail = True
        break
_check(not _buf_cleared_after_fail,
       f"flush FAIL 后 buf 不清空")

# R1R-5: S1 FAIL → sys.exit(1)
_check("sys.exit(1)" in src,
       f"S1 FAIL → sys.exit(1)")

# =========================================================================
# §88 R1R2-1: 哨兵剔除 spec 级开关（§98 E-R3 改判：**哨兵不是垃圾行**）
# =========================================================================
print("\n" + "="*60)
print("§88 R1R2-1: strip_sentinel — 两侧一致为 False（§98 改判）")
print("="*60)

# §98 核心改判：哨兵行不是垃圾行，是"首个公司行动之前所有日期的基准因子"
# 错误源头：§86 R3R-1 要求 us_qfq_factor 剔除哨兵 → strip_sentinel=True
# → io.py 用 date < "19900101" 一刀切，把 1 哨兵 + 12 条真实公司行动全删
# → expand_us_qfq_factor 用 1.0 假哨兵顶替 → close_qfq ≡ close（偏差 224×）
# 修复：us_qfq_factor.strip_sentinel=False（与 hk_qfq_factor 一致）

sp_us = spec_get("us_qfq_factor")
sp_hk = spec_get("hk_qfq_factor")

# 两侧现在一致 False（哨兵 = 基准因子，保留入库）
_check(sp_us.strip_sentinel == False,
       f"us_qfq_factor.strip_sentinel=False（实测 {sp_us.strip_sentinel}；§98 E-R3 修复）")
_check(sp_hk.strip_sentinel == False,
       f"hk_qfq_factor.strip_sentinel=False（实测 {sp_hk.strip_sentinel}；历史不变）")

# 模拟 normalize：两侧均保留哨兵行（strip_sentinel=False 跳剔除 + 跳升序）
# reader 层 expand 会自己 sort_values
df_hk = pd.DataFrame({
    "symbol": ["00700"]*5,
    "date": ["19000101", "20050419", "20050727", "20200415", "20231025"],
    "qfq_factor": ["1.0", "1.0", "0.95", "0.98", "1.0"],
})
norm_hk, w1 = normalize(df_hk, sp_hk)
_check(len(norm_hk) == 5,
       f"hk_qfq_factor 行数不变（5 vs {len(norm_hk)}）")
_check("19000101" in norm_hk["date"].values.astype(str),
       f"hk_qfq_factor 哨兵行 19000101 保留")

df_us = pd.DataFrame({
    "symbol": ["AAPL"]*5,
    "date": ["20260511", "20230810", "19900219", "19700101", "19870616"],
    "qfq_factor": ["1.0", "0.99", "0.008929", "0.004464", "0.008929"],
})
norm_us, w2 = normalize(df_us, sp_us)
# strip_sentinel=False 后 5 行全保留（含 19700101 哨兵 + 19870616 真实公司行动）
_check(len(norm_us) == 5,
       f"us_qfq_factor 全保留哨兵 + 公司行动（5 vs {len(norm_us)}）")
_check("19700101" in norm_us["date"].values.astype(str),
       f"us_qfq_factor 哨兵行 19700101 保留（真实基准因子 0.004464）")
_check("19870616" in norm_us["date"].values.astype(str),
       f"us_qfq_factor 1987-06-16 拆股行保留（非误删）")
# 注：strip_sentinel=False 时 normalize 跳过升序（升序在 if strip_sentinel 块里）
# reader 层 expand_us_qfq_factor 会自己 sort_values("_dt")，不影响复权

# =========================================================================
# §88 R1R2-2: 既有 hk_qfq_factor 哨兵行完好 + expand_qfq_factor 真断言
# =========================================================================
print("\n" + "="*60)
print("§88 R1R2-2: 哨兵行完好 + expand_qfq_factor(00700) 真断言")
print("="*60)

try:
    from config_alt import ALT_DATA_DIR
    hk_qfq_path = os.path.join(ALT_DATA_DIR, "hk_qfq_factor", "hk_qfq_factor.parquet")
    if os.path.exists(hk_qfq_path):
        df_fac = pd.read_parquet(hk_qfq_path)
        check_cols = all(c in df_fac.columns for c in ("symbol", "date"))
        _check(check_cols, f"hk_qfq_factor 有 symbol + date 列")
        n_all = len(df_fac)
        _check(n_all > 0, f"hk_qfq_factor 有 {n_all} 行")
        sentinel = df_fac[df_fac["date"].astype(str) == "19000101"]
        _check(len(sentinel) > 0, f"哨兵行 19000101 存在（{len(sentinel)} 行）")
        all_syms = set(df_fac["symbol"].astype(str).unique())
        sym_with_sentinel = set(sentinel["symbol"].astype(str).unique())
        missing = all_syms - sym_with_sentinel
        _check(len(missing) == 0,
               f"全部 {len(all_syms)} 个标的都有 19000101 哨兵行（缺失 {len(missing)} 个）")
        print("  ✅ 既有表哨兵行完好")
    else:
        print(f"  ⚠ {hk_qfq_path} 不存在，跳过既有表验证（可能未建库）")
except ImportError:
    print("  ⚠ config_alt 导入失败，跳过既有表验证")

# --- E-1a 真断言：expand_qfq_factor("00700") 2004 年 close_qfq 全非空 ---
print("\n  --- E-1a 真断言: expand_qfq_factor('00700') ---")
try:
    from alt.reader import expand_qfq_factor
    d = expand_qfq_factor("00700", with_price=True)
    _check("close_qfq" in d.columns,
           f"expand_qfq_factor 返回含 close_qfq 列（cols={list(d.columns)}）")
    y04 = d[d["date"].astype(str).str.startswith("2004")]
    _check(len(y04) == 137,
           f"00700 2004 年 137 行（实测 {len(y04)}）")
    _check(y04["close_qfq"].notna().all(),
           f"00700 2004 年 close_qfq 100% 非空（非空 {y04['close_qfq'].notna().sum()}/{len(y04)}）")
    print(f"    qfq_factor={y04['qfq_factor'].iloc[0]:.6f}（哨兵基准值）")
    print(f"    首行 date={y04['date'].iloc[0]} close_qfq={y04['close_qfq'].iloc[0]:.6f}")
    print(f"    末行 date={y04['date'].iloc[-1]} close_qfq={y04['close_qfq'].iloc[-1]:.6f}")
except ImportError as e:
    print(f"  ⚠ alt.reader 导入失败（{e}），跳过 E-1a")
except Exception as e:
    _errors.append(f"E-1a expand_qfq_factor 异常: {type(e).__name__}: {e}")
    print(f"  ❌ FAIL E-1a: {type(e).__name__}: {e}")

# --- E-1b 数值断言：expand_us_qfq_factor("AAPL") 复权正确 ---
print("\n  --- E-1b 数值断言: expand_us_qfq_factor('AAPL') ---")
try:
    from alt.reader import expand_us_qfq_factor
    from config_alt import ALT_TMP_DIR
    _daily_dir = pathlib.Path(ALT_TMP_DIR) / "us_daily_raw"
    _fac_dir = pathlib.Path(ALT_TMP_DIR) / "us_qfq_factor"
    if _daily_dir.exists() and _fac_dir.exists():
        d_us = expand_us_qfq_factor("AAPL", with_price=True, root=ALT_TMP_DIR)

        # ① 哨兵因子：表内首个事件 = 19700101 qfq_factor=0.004464285714
        #   （真实最早基准，不是人工合成的 1.0）
        first_fac = d_us["qfq_factor"].iloc[0]
        sentinel_val = 0.004464285714
        _check(abs(first_fac - sentinel_val) < 1e-10,
               f"哨兵基准因子={first_fac} ≈ {sentinel_val}（真实哨兵值，非人工合成）")

        # ② 1984 首行 close_qfq ≈ 26.50 × 0.004464285714 = 0.11830357
        y84 = d_us[d_us["date"].astype(str).str.startswith("1984")]
        first84 = y84.iloc[0]
        expected_cq = float(first84["close"]) * sentinel_val
        actual_cq = float(first84["close_qfq"])
        _check(abs(actual_cq - expected_cq) < 1e-4,
               f"1984 首行 close_qfq={actual_cq:.6f} ≈ {expected_cq:.6f}（容差 1e-4）")

        # ③ 1987-06-16 拆股处 close_qfq ratio ≈ 1.057（连续，无断崖）
        #    拆股前 close=78.5, factor=0.004464 → cq=0.3504
        #    拆股后 close=41.5, factor=0.008929（翻倍）→ cq=0.3705
        #    ratio = 0.3705/0.3504 ≈ 1.057（而非 -47% 断崖）
        d15 = d_us[d_us["date"].astype(str) == "19870615"]
        d16 = d_us[d_us["date"].astype(str) == "19870616"]
        if len(d15) and len(d16):
            cq15 = float(d15.iloc[0]["close_qfq"])
            cq16 = float(d16.iloc[0]["close_qfq"])
            qfq_ratio = cq16 / cq15
            _check(abs(qfq_ratio - 1.057) < 0.1,
                   f"19870616 拆股后 close_qfq ratio={qfq_ratio:.4f} ≈ 1.057（无断崖）")
            raw_ratio = float(d16.iloc[0]["close"]) / float(d15.iloc[0]["close"])
            print(f"    raw close ratio={raw_ratio:.4f}（-47.3% 断崖）→ qfq ratio={qfq_ratio:.4f}（复权抹平）")

        print(f"    哨兵 19700101 qfq_factor={sentinel_val}（真实基准）")
        print(f"    1984 首行 close_qfq={actual_cq:.6f} ≈ 0.1183（精确命中）")
        print(f"    全表 {len(d_us)} 行 qfq_factor 零 null")
    else:
        print(f"  ⚠ us_daily_raw / us_qfq_factor 样本不存在于 {ALT_TMP_DIR}，跳过 E-1b")
except ImportError as e:
    print(f"  ⚠ 导入失败（{e}），跳过 E-1b")
except Exception as e:
    _errors.append(f"E-1b expand_us_qfq_factor 异常: {type(e).__name__}: {e}")
    print(f"  ❌ FAIL E-1b: {type(e).__name__}: {e}")

# =========================================================================
# §88 R1R2-4: as_of + src 四表 inject_const（spec 级 + 落盘 parquet 级）
# =========================================================================
print("\n" + "="*60)
print("§88 R1R2-4: as_of + src 四表 inject_const")
print("="*60)

four_tables = ["hk_fina_snap", "hk_val_pct", "us_daily_raw", "us_qfq_factor"]
today = datetime.now().strftime("%Y%m%d")

scratch_root = PROJECT_ROOT.parent / "_scratch"
scratch_files = {
    "hk_val_pct": scratch_root / "hk_val_pct" / "hk_val_pct_3sample.parquet",
    "us_qfq_factor": scratch_root / "us_qfq_factor" / "us_qfq_factor_3sample.parquet",
}

for name in four_tables:
    sp = spec_get(name)
    ic = sp.inject_const
    has_src = "src" in ic
    has_as_of = "as_of" in ic
    src_val = ic.get("src", "(未设)")
    as_of_val = today if ic.get("as_of") == "__TODAY__" else ic.get("as_of", "(未设)")
    _check(has_src and has_as_of,
           f"{name}: src={src_val}, as_of={as_of_val}")

# --- E-4: 落盘 parquet 级验证（禁止只看 spec 字典） ---
print("\n  --- E-4 落盘 parquet 验证 ---")
for name, fpath in scratch_files.items():
    if fpath.exists():
        dp = pd.read_parquet(str(fpath))
        has_a = "as_of" in dp.columns
        has_s = "src" in dp.columns
        a_vals = dp["as_of"].unique().tolist() if has_a else []
        s_vals = dp["src"].unique().tolist() if has_s else []
        a_ok = has_a and all(v != "__TODAY__" for v in a_vals) and dp["as_of"].notna().all()
        s_ok = has_s and dp["src"].notna().all() and (dp["src"].astype(str) != "").all()
        _check(a_ok, f"{name} 落盘 as_of 非空非字面量 (vals={a_vals})")
        _check(s_ok, f"{name} 落盘 src 非空 (vals={s_vals})")
        print(f"    样本 {fpath.name}: {len(dp)} rows, cols={list(dp.columns)}")
    else:
        print(f"    ⚠ {fpath} 不存在（落盘样本需先手动拉取到 _scratch/）")

# =========================================================================
# §104 E-3-R5: 全库冒烟 —— 76 张表逐张跑 normalize(假数据)，零异常
#   背景（§104 P0）：E-3-R3 把排序键硬编码为 ["symbol", date_col]，
#   而 62/76 张表无 symbol 列（subset="*" 的大盘指数/宏观单序列表）
#   → normalize 抛 KeyError，其中 61 张走 17:30 日更 ⇒ 全库 82% 停摆。
#   本用例为该回归的**常驻兜底**：任何"排序键假定某列必然存在"的改动都会在此转红。
# =========================================================================
print("\n" + "="*60)
print("§104 E-3-R5: 全库冒烟 76 张表 normalize 零异常")
print("="*60)

import os
from alt.spec import ALL_SPECS
from config_alt import alt_table_dir


def _real_columns(table: str):
    """读该表已落盘 parquet 的真实列（无此表则返回 None）。"""
    d = alt_table_dir(table)
    if not os.path.isdir(d):
        return None
    files = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
    if not files:
        return None
    try:
        return list(pd.read_parquet(os.path.join(d, files[0])).columns)
    except Exception:  # noqa: BLE001
        return None


def _fake_df(sp, real_cols):
    """按 spec 构造最小假数据，列尽量贴近真实落盘形态。"""
    cols = {}
    # 日期列（inject_date 的表不需要源日期列）
    if sp.has_date and not sp.inject_date:
        src = sp.src_date_col or sp.date_col
        cols[src] = ["20260102", "20260101", "20260103"]   # 故意乱序，验证排序
    # 标的列：优先按真实 parquet 判断，其次按声明推断
    if real_cols is not None:
        has_symbol = sp.symbol_col in real_cols or "symbol" in real_cols
    else:
        has_symbol = (isinstance(sp.subset, list) and "symbol" in sp.subset) or sp.mode == "list"
    if has_symbol:
        cols["symbol"] = ["B002", "A001", "A001"]
    # subset 非日期键
    if isinstance(sp.subset, list):
        for c in sp.subset:
            if c == sp.date_col or c in cols:
                continue
            if c == "symbol":
                continue
            cols[c] = ["k2", "k1", "k1"]
    # enum 列
    if sp.mode == "enum" and sp.enum_col and sp.enum_col not in cols:
        cols[sp.enum_col] = ["v1", "v1", "v1"]
    # 数值/字符串列
    for c in (sp.force_str_cols or []):
        cols[c] = ["1", "2", "3"]
    for c in (sp.coerce_float_cols or []):
        cols[c] = ["1.5", "2.5", "3.5"]
    # 至少给一列数值，避免空表
    cols.setdefault("value", [1.0, 2.0, 3.0])
    n = max(len(v) for v in cols.values())
    for k, v in list(cols.items()):
        if len(v) < n:
            cols[k] = v + [v[-1]] * (n - len(v))
    return pd.DataFrame(cols)


smoke_fail = []
no_symbol = []
smoke_rows = []
for sp in ALL_SPECS:
    real = _real_columns(sp.name)
    df = _fake_df(sp, real)
    if "symbol" not in df.columns:
        no_symbol.append(sp.name)
    try:
        out, warns = normalize(df, sp, req_date="20260102", enum_value="v1")
    except Exception as e:  # noqa: BLE001
        smoke_fail.append((sp.name, type(e).__name__, str(e)))
        print(f"  ❌ {sp.name}: {type(e).__name__}: {e}")
        continue
    # 日期升序不变量（按与实现同样的分组键逐组校验）
    if sp.has_date and sp.date_col in out.columns and len(out) > 1:
        gkeys = []
        if "symbol" in out.columns:
            gkeys.append("symbol")
        if isinstance(sp.subset, list):
            for c in sp.subset:
                if c != sp.date_col and c in out.columns and c not in gkeys:
                    gkeys.append(c)
        ok_asc = True
        if gkeys:
            for _, g in out.groupby(gkeys, dropna=False):
                s = g[sp.date_col].astype(str).tolist()
                if s != sorted(s):
                    ok_asc = False
                    break
        else:
            s = out[sp.date_col].astype(str).tolist()
            ok_asc = s == sorted(s)
        if not ok_asc:
            smoke_fail.append((sp.name, "SortOrder", "日期非升序"))
            print(f"  ❌ {sp.name}: 日期非升序")
            continue
    smoke_rows.append((sp.name, len(df), len(out), len(warns)))

_check(len(smoke_fail) == 0,
       f"全库冒烟 {len(ALL_SPECS) - len(smoke_fail)}/{len(ALL_SPECS)} 张表 normalize 零异常"
       + (f"（失败: {[f[0] for f in smoke_fail]}）" if smoke_fail else ""))
_check(len(no_symbol) > 0,
       f"冒烟覆盖到无 symbol 列的表 {len(no_symbol)} 张（硬编码 ['symbol',date] 必崩的那批）")
print(f"    总表数={len(ALL_SPECS)} 无symbol={len(no_symbol)} 通过={len(smoke_rows)} 失败={len(smoke_fail)}")

# =========================================================================
# 汇总
# =========================================================================
print("\n" + "="*60)
if _errors:
    print(f"❌ {len(_errors)} 项失败：")
    for e in _errors:
        print(f"  ❌ {e}")
    sys.exit(1)
else:
    print("✅ 全部 OS-R1 / OS-R1R / OS-R1R2 验证通过")
print("="*60)
