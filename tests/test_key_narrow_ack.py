# -*- coding: utf-8 -*-
"""CC-FIX-8 v2（§369/§372）：KEY_NARROW_ACK 的显式裁定与增长护栏。"""
from config.tables import KEY_NARROW_ACK
from tools.db.audit import audit_duplicates


def _verdict(name):
    rows = audit_duplicates(only=(name,), verbose=False)
    return next(r["verdict"] for r in rows if r["name"] == name)


def test_index_global_acked():
    """现盘 index_global → ✓ 已裁定（文案含基线 131）。"""
    v = _verdict("index_global")
    assert v.startswith("✓ 已裁定：上游修订"), v
    assert "基线 131" in v


def test_alarm_when_baseline_exceeded(monkeypatch):
    """把 baseline_dup 临时改小（10）→ 必须报 ⚠ 异常增长。"""
    monkeypatch.setitem(KEY_NARROW_ACK["index_global"], "baseline_dup", 10)
    v = _verdict("index_global")
    assert v.startswith("⚠ 已裁定表同键多行异常增长"), v
    assert "10×1.5=15" in v


def test_dividend_not_acked():
    """dividend 不在 ACK 里（防日后顺手塞入）。

    CC-FIX-9（§375）折叠后 dividend 应判「✓ 无真重复」；本测试即复发报警：
    唯一放行档为「✓ 无真重复」——同键不同值（⚠ 键可能不足）、真重复（⚠）、
    上游登记（ℹ）任何一档出现都须红。
    CC-FIX-11（§376）由「not ✓ 已裁定」收紧（该断言放行 ℹ/⚠ 三档，报警失效）。
    """
    assert "dividend" not in KEY_NARROW_ACK
    v = _verdict("dividend")
    assert v.startswith("✓ 无真重复"), v
