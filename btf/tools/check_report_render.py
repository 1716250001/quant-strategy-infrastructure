# -*- coding: utf-8 -*-
"""bt check 第 7 项：**报告渲染级结构机检**（Z-5，19 号 §26.2 / §27.2 铁律新 14）。

由 `bt check` 第 7/9 项编排（亦可 `python tools/check_report_render.py`；单跑仅用于定位）。


为什么需要（第六种逃逸模式）：
    P1-NEW-7「报告正文整体被转义」自报告功能诞生起存在，逃过 6 轮审查 + 765
    测试——因为所有断言查的都是**文件字节/文本子串**，而转义**只动标签、不动
    文本**（`id="summary"` 在、`"next_open"` 在、CLI 与 api 报告仍字节相等）。
    本条机检落到**渲染后的结构**，是本类缺陷的常跑守卫。

**变异性检查（AA-1，铁律新 15；19 号 §30.8.1 / §31.3）**：
    第 6 项（"纯文本字段仍转义"）原判据 `"<script>alert" not in html` **恒为真**——
    合成 bundle 的 `title` 从无 HTML 字符 ⇒ fixture 永不触发该断言 ⇒ **伪防线**
    （第七种逃逸模式：断言写法正确、输入从不触发）。现 fixture **故意注入**
    `<script>alert(1)</script>` 到 `title`（纯文本字段）：
      - 生产态：该串必须**被转义**（`&lt;script&gt;…`）、且裸串**不存在** ⇒ 真断言；
      - 负向对照：把模板 `{{ title }}` 误改 `| safe` → 本项**必须变红**（已验证）。

判据（无需主库；合成产物，秒级）：
    - 指标卡成形：`<div class='cards'>` 与 `<div class='card'>` 真实存在
    - 表格成形：`<table><thead>`
    - 图表脚本存在：`<script` 计数 ≥ 1（六图内联）
    - 无整体转义残留：`&lt;div class=` 计数为 0
    - 纯文本字段**仍应转义**：注入探针被转义 + 裸串不存在（AA-1：真断言）

用法:
    python tools/check_report_render.py              # 生产态机检（退出码 0=通过）
    python tools/check_report_render.py --self-test  # **变异性检查**（铁律新 15）：
                                                     两处故意破坏 → 断言必须变红
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

#: 纯文本字段注入探针（AA-1）：**故意含 HTML**，使"仍转义"断言可被证伪
PROBE = "<script>alert(1)</script>"
ESCAPED_PROBE = "&lt;script&gt;alert(1)&lt;/script&gt;"


def _bundle():
    """最小合成产物（不触主库/不落盘；仅报告编排所需字段）。"""
    from btf.experiment.manifest import RunManifest
    from btf.experiment.store import SnapshotRecord

    days = ["20240102", "20240103", "20240104"]
    navs = [1_000_000.0, 1_010_000.0, 1_005_000.0]
    snapshots = [
        SnapshotRecord(date=d, cash=100_000.0, market_value=v - 100_000.0,
                       total_value=v, daily_return=v / navs[0] - 1.0,
                       cumulative_return=v / navs[0] - 1.0, drawdown=0.0,
                       weights={"000001.SZ": 0.9}, positions_qty={"000001.SZ": 100})
        for d, v in zip(days, navs, strict=True)
    ]
    # trades/fills/rejections 在产物中是**行 dict**（报告与图表按 dict 消费）
    trades = [{"trade_id": "T1", "symbol": "000001.SZ", "qty": 100,
               "pnl": 1.0, "open_date": days[0], "close_date": days[1]}]
    manifest = RunManifest(run_id="check_render", status="COMPLETED",
                           config_hash="sha256:check",
                           config_effective={"run": {"universe": {"source": "explicit"}}})
    return type("Bundle", (), {
        "manifest": manifest, "metrics": {"total_return": 0.005,
                                          "final_nav": navs[-1]},
        "snapshots": snapshots, "trades": trades, "fills": [], "rejections": [],
        "run_id": manifest.run_id,
    })()


def _run_checks() -> tuple[bool, list[tuple[str, bool]]]:
    """跑 6 项渲染级判据（title 注入 HTML 探针——AA-1）。"""
    from btf.viz.report import ReportBuilder

    # title 是**纯文本字段**：注入 HTML 探针（AA-1）——生产态必须被转义
    html = ReportBuilder(_bundle(), title=f"回测报告 · {PROBE}").build()
    checks = [
        ("指标卡成形 <div class='cards'>", "<div class='cards'>" in html),
        ("指标卡项 <div class='card'>", "<div class='card'>" in html),
        ("表格成形 <table><thead>", "<table><thead>" in html),
        ("图表脚本存在 <script ≥1", html.count("<script") >= 1),
        ("无整体转义残留（&lt;div class=）", "&lt;div class=" not in html),
        ("纯文本字段仍转义（注入探针被转义且裸串不存在）",
         PROBE not in html and ESCAPED_PROBE in html),
    ]
    ok = all(p for _n, p in checks)
    print(f"  渲染级结构：{'通过' if ok else '失败'}"
          f"（<div={html.count('<div')} <script={html.count('<script')} "
          f"转义&lt;div={html.count('&lt;div')}"
          f" 探针裸奔={PROBE in html} 探针转义={ESCAPED_PROBE in html}）")
    return ok, checks


def _self_test() -> int:
    """**变异性检查**（铁律新 15）：故意破坏被保护对象 → 断言必须变红。

    两处对照（都在内存里改模板，不落盘）：
        A. `{{ section.html | safe }}` → `{{ section.html }}`（整体转义）
           ⇒ 前 5 项必须变红（轮次七负向对照 A 的固化版）；
        B. `{{ title }}` → `{{ title | safe }}`（纯文本字段误加 safe）
           ⇒ 第 6 项必须变红（AA-1：原"恒真伪防线"修复后的证明）。
    破坏后仍恒绿 = 伪防线 → 本自检失败（退出码 1）。
    """
    import btf.viz.report as report

    fixed = report._TEMPLATE
    cases = [
        ("A 整体转义（section.html 去 safe）",
         "{{ section.html | safe }}", "{{ section.html }}", 0),
        ("B 纯文本误加 safe（title）",
         "{{ title }}", "{{ title | safe }}", 5),
    ]
    ok = True
    for name, old, new, probe_index in cases:
        assert old in fixed, f"模板缺少锚点：{old}"
        report._TEMPLATE = fixed.replace(old, new, 1)
        try:
            passed, checks = _run_checks()
        finally:
            report._TEMPLATE = fixed
        red = not passed and not checks[probe_index][1]
        print(f"  变异性检查 {name}："
              f"{'PASS（断言已变红）' if red else 'FAIL（仍恒绿＝伪防线）'}"
              f" → 目标项 [{probe_index}] = {checks[probe_index][1]}")
        ok = ok and red
    # 还原后必须回到通过（防"自检把模板改坏"的次生风险）
    restored, _checks = _run_checks()
    print(f"  还原后生产态：{'PASS' if restored else 'FAIL'}")
    return 0 if (ok and restored) else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:
        print("=== 变异性检查（铁律新 15：防线须可被证伪）===")
        return _self_test()
    passed, checks = _run_checks()
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
