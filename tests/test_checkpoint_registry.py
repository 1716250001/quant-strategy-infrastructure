# -*- coding: utf-8 -*-
"""断点登记门面守卫（P3-2／P3-2b）
==================================
判据来源：`派单-P3-2门面与守卫-正式施工单-20261111.md` §3.3 的 T1–T5，
外加 K4 的两个负例（N1 未知域、N2 文件缺失）。

纪律：本文件只**断言**，不改任何生产代码；失败信息必须写清「谁违反了哪条」，
不要只报 assert False —— 半年后看到红灯的人要能直接定位。
"""
import fnmatch
import pathlib
import re

import pytest

from config import MARKET_DATA_DIR
from config import checkpoint_registry as _cfg
from tools.db import audit
from tools.db import checkpoint_registry as _pg

CODE_ROOT = pathlib.Path(__file__).resolve().parents[1]

# T5 基线：2026-11-11 复跑实测 **15 处 / 10 文件**（口径见 `_direct_writes`）。
# ⚠ 只许下降不许上升：新增直写必须走 common/parquet_store.py。
# ⚠ 与派单里「22 处」的差 = `parquet_store.py` 自身 1 处 + `tests/` 内 2 处
#   + `btf/` 子仓与注释/字符串命中；差异已写进回执待赤潮确认。
BASELINE_DIRECT_WRITES = 15

# T3 反向扫描的匹配面（派单 §3.3-T3 原文）
_CHECKPOINT_PATTERNS = ("*checkpoint*", "*_state.json", "*_progress.json")

# T3 已知未登记清单：**不是白名单，是待裁定清单**。
# 新增任何一项都必须先经赤潮裁定，否则 test_t3 会红（防静默放行）。
KNOWN_UNREGISTERED = {
    ".daily_refill_progress.json": (
        "P3-2b 反向扫描发现：数据根有此文件、registry 40 域均未登记，"
        "且全仓 .py 无任何写入点（mtime 却是当天 ⇒ 有仓库外进程在写）。"
        "补登记 / 归 NOT_A_CHECKPOINT / 判孤儿，待赤潮裁定。"
    ),
}


def _checkpoint_like(root):
    """扫 root 下所有「像断点」的文件（相对路径 posix 化），排除 *.lock。"""
    root = pathlib.Path(root)
    out = []
    for p in root.rglob("*"):
        if not p.is_file() or p.name.endswith(".lock"):
            continue
        if any(fnmatch.fnmatch(p.name, pat) for pat in _CHECKPOINT_PATTERNS):
            out.append(p.relative_to(root).as_posix())
    return sorted(out)


def _registered_files():
    return {d.file for d in _cfg.all_domains()}


def _direct_writes():
    """列出绕过 parquet_store 的 `.to_parquet(` 直写点（相对路径, 行号, 行）。

    排除：虚拟环境、测试、临时草稿、btf（独立子仓）、缓存，
    以及 `common/parquet_store.py` 本身（它就是唯一允许直写的必经之地）。
    """
    skip = {"_venv", ".git", "__pycache__", "_scratch", "tests",
            "btf", ".pytest_cache", "_wt_d3_before"}
    out = []
    for p in CODE_ROOT.rglob("*.py"):
        if any(part in skip for part in p.parts) or p.name == "parquet_store.py":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if ".to_parquet(" in line:
                out.append((str(p.relative_to(CODE_ROOT)), i, line.strip()))
    return out


# ---------------------------------------------------------------- T1 汇总
def test_t1_describe_summary():
    """T1：全库汇总的域总量、权威分布、库分布、三元组数必须与登记表逐字一致。"""
    s = _pg.describe()
    assert s["n_domains"] == 43
    # DEAD：W-2 补登记 3 张零载体表（bc_bestotcqt/stk_mins/report_rc，§449.4）。
    assert s["by_authority"].get("DEAD", 0) == 3, f"DEAD 应为 3：{s['by_authority']}"
    for auth, n in {"PRIMARY": 16, "SUPPLEMENTARY": 14,
                    "LEGACY": 5, "NOT_A_CHECKPOINT": 5}.items():
        assert s["by_authority"].get(auth) == n, f"权威分布漂移：{s['by_authority']}"
    assert set(s["by_authority"]) <= {
        "PRIMARY", "SUPPLEMENTARY", "LEGACY", "DEAD", "NOT_A_CHECKPOINT"}, \
        f"出现了未声明的权威等级：{s['by_authority']}"
    assert s["by_library"] == {"market_data": 21, "alt_data": 22}, \
        f"库分布漂移：{s['by_library']}"
    assert s["primary_triples"] == 216, f"PRIMARY 三元组应为 216：{s['primary_triples']}"
    assert sum(s["by_key_space"].values()) == s["primary_triples"]


# ---------------------------------------------------------- T2 唯一性
def test_t2_primary_uniqueness():
    """T2：同 (library, table, key_space) 的 PRIMARY 恰 1 个；三元组总数 303。"""
    triples = [(d.library, t, d.key_space) for d in _cfg.all_domains() for t in d.tables]
    assert len(set(triples)) == 306, f"三元组应为 306 个，实测 {len(set(triples))}"

    prim = {}
    for d in _cfg.all_domains():
        if d.authority != "PRIMARY":
            continue
        for t in d.tables:
            prim.setdefault((d.library, t, d.key_space), []).append(d.id)
    dup = {k: v for k, v in prim.items() if len(v) > 1}
    assert not dup, f"以下三元组有多个 PRIMARY（违反唯一性规则 1）：{dup}"
    assert len(prim) == 216, f"PRIMARY 三元组应为 216，实测 {len(prim)}"


# ------------------------------------------------- T3 反向归属（核心）
def test_t3_reverse_attribution():
    """T3：磁盘上每个「像断点」的文件都必须能在 registry 找到归宿。

    正向唯一性（T2）是自洽检查，抓不到「磁盘有新文件但没人登记」；
    本用例反向扫磁盘，未登记即红 —— 这是 P3-2b 的唯一否决点（N3）。
    """
    registered = _registered_files()
    found = _checkpoint_like(MARKET_DATA_DIR)
    assert len(found) >= 8, f"扫描面异常（只扫到 {len(found)} 个），先确认数据根路径"
    unregistered = [
        f for f in found
        if f not in registered and pathlib.PurePosixPath(f).name not in registered
    ]
    unknown = [f for f in unregistered if f not in KNOWN_UNREGISTERED]
    assert not unknown, (
        f"磁盘有 {len(unknown)} 个断点类文件未登记：{unknown}；"
        f"要么在 config/checkpoint_registry.py 补登记，要么由赤潮裁定归 NOT_A_CHECKPOINT"
    )
    vanished = [f for f in KNOWN_UNREGISTERED if f not in unregistered]
    assert not vanished, (
        f"KNOWN_UNREGISTERED 中的 {vanished} 已登记或已删除 —— 请同步删除该条目，"
        f"否则这张表会退化成静默白名单"
    )


def test_t3b_scan_detects_planted_file(tmp_path):
    """T3 自检：造一个未登记的假断点文件，扫描必须报出，且 *.lock 必须排除。"""
    (tmp_path / ".fake_checkpoint.json").write_text("{}", encoding="utf-8")
    (tmp_path / "x.lock").write_text("", encoding="utf-8")
    found = _checkpoint_like(tmp_path)
    assert ".fake_checkpoint.json" in found, "假断点文件未被扫出 ⇒ 反向归属失效"
    assert "x.lock" not in found, "*.lock 不该进扫描面"


# ------------------------------------------------------- T4 空 tables 容忍
def test_t4_empty_tables_tolerated():
    """T4：只登记文件、不声明表的 7 个域，describe 不得崩（禁 min()/[0] 取首元素）。"""
    ids = [
        "market.backfill.bak_20260925", "market.backfill.bak_20261001",
        "alt.meta.audit_report", "alt.meta.spec_verify_report", "alt.meta.isolation_log",
        "alt.meta.main_db_snapshot", "market.meta.db_version",
    ]
    for i in ids:
        one = _pg.describe(i)
        assert one["n_tables"] == 0, f"{i} 应为空表集"
        assert one["tables"] == []


# --------------------------------------------------- T5 直写 parquet 守卫
def test_t5_direct_write_guard():
    """T5（数量）：直写点不得增加（弱守卫，只守「变多」）。"""
    sites = _direct_writes()
    assert len(sites) <= BASELINE_DIRECT_WRITES, (
        f"直写点 {len(sites)} > 基线 {BASELINE_DIRECT_WRITES}："
        f"新增 .to_parquet 必须走 common/parquet_store.py；"
        f"违规文件: {sorted({s[0] for s in sites})}"
    )


# NOTE（2026-11-11，老大裁定 / 18 号 §410.7）：原 test_t5b_ack_annotations 已删除。
# 它曾以 xfail(strict=True) 存在 —— 等于把「每处直写须带 # DB-WRITE-ACK」这条判据
# 单方面降级为「不要求」，并以 `1 xfailed` 长期污染「全绿」基线。
# 现状实测 0/15 处带该注释；补注释须改 10 个白名单外文件，超出 P3-2b 白名单。
# ⇒ ACK 判据移交 G7（存储层收口）重新评估：补注释，还是改用 parquet_store 强制收口。
# ⛔ 不要在此重新加回 xfail —— 要恢复须从 G7 走正式判据变更流程。


# ------------------------------------------------------------ 负例（K4）
def test_n1_unknown_domain_raises():
    """N1：不存在的域必须当场 KeyError，且带最接近候选（防 typo 静默）。"""
    with pytest.raises(KeyError, match="最接近"):
        _pg.get("market.backfill.by_dates")
    with pytest.raises(KeyError):
        _pg.get("fut_index_daily.x")
    with pytest.raises(KeyError):
        _pg.describe("no.such.domain")


def test_n2_missing_file_is_false(monkeypatch, tmp_path):
    """N2：文件不存在时 describe 返回 file_exists=False，不得抛异常。"""
    monkeypatch.setattr(_pg, "_lib_root", lambda library: str(tmp_path))
    one = _pg.describe("market.backfill.by_date")
    assert one["file_exists"] is False
    assert one["file_mtime"] is None


def test_timestamp_none_or_iso():
    """时间戳口径：要么 None，要么严格 ISO（不许把 dict/空串透出去）。"""
    ts = _pg.timestamp("alt.ckpt.__whole__", "stk_etf")
    assert ts is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)


# ------------------------------------------------- K2 覆盖面（§409.2 返工）
def test_k2_audit_checks_all_carriers(monkeypatch):
    """K2：audit 必须**真核**每个载体，而不是只打印 21 个、实际核 3 个。

    §409.2 的教训：上一版只断言 `_checkpoint_files()` 返回 21，没测「是否真的核」，
    于是三道 continue 把 18 个载体全跳过而测试仍绿（测试绿 ≠ 判据对）。
    这里用 spy 数 `_check_carrier` 的实际调用次数与深度。
    """
    calls, depths = [], []
    orig = audit._check_carrier

    def spy(dom):
        calls.append(dom.file)
        res = orig(dom)
        depths.append(res[1])
        return res

    monkeypatch.setattr(audit, "_check_carrier", spy)
    errors, _infos = audit.audit_checkpoint(verbose=False)
    assert len(calls) == len(audit._checkpoint_files()) == 21, \
        f"只核了 {len(calls)} 个载体（应 21）"
    assert depths.count("deep") >= 6, f"深度核对仅 {depths.count('deep')} 个，K2 要求 ≥6"
    assert not errors, f"真实数据上不应有真异常：{errors}"
