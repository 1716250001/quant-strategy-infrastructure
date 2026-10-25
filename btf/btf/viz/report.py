# -*- coding: utf-8 -*-
"""ReportBuilder：Jinja2 单文件 HTML 报告（13 §19.4/19.5；M2 任务 5.8）。

章节（13 §19.4 顺序，章节清单可由 charts 参数裁剪但**假设与披露不可裁**）：
    封面 → 执行摘要（指标卡）→ 绩效分析（图1/2/3 + 指标表）→
    交易分析（交易表 + 权重图 + 换手/费用/拒单分布）→
    **假设与披露（强制章节）** → 复现说明 → 附录（配置脱敏全文）

强制章节纪律（评审规则）：无"假设与披露"的报告**视为不可信**——
`include_assumptions=False` 直接抛 ReportError（不静默省略）。

幂等（13 §19.5）：同 RunBundle + 同 Assumptions → HTML **逐字节一致**
（不含生成时间戳；时间信息一律取自 manifest）。

分层纪律（铁律 5）：本报告只消费 RunStore 产物 + 调用方注入的假设数据，
不 import 引擎/执行内部（费率分段等由调用方注入，viz 不读实现）。
"""
from __future__ import annotations

import html
import json
import pathlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from btf.viz.charts import DEFAULT_CHARTS, build_chart
from btf.viz.render import plotly_available, render_html


class RunResultView(Protocol):
    """RunStore 产物视图（结构化契约；viz **不 import experiment**——铁律 5）。

    由 `RunBundle`（experiment/store.py）或任何同字段对象满足：表现层只认
    数据契约，报告生成因此与 RunStore 实现解耦（13 §19.1）。
    """

    manifest: Any                                  # RunManifest（含四版本字段）
    metrics: Mapping[str, float]
    snapshots: Sequence[Any]                       # SnapshotRecord 序列
    trades: Sequence[Mapping[str, Any]]
    fills: Sequence[Mapping[str, Any]]
    rejections: Sequence[Mapping[str, Any]]

try:
    from jinja2 import Environment, StrictUndefined
except ImportError:                     # pragma: no cover
    Environment = None                  # type: ignore[assignment]
    StrictUndefined = None              # type: ignore[assignment]


class ReportError(RuntimeError):
    """报告构建违例（如强制章节缺失）。"""


@dataclass(frozen=True)
class Assumptions:
    """假设与披露数据（调用方注入——费率分段/降级事件来自引擎侧知识）。"""

    matching: str = "next_open"                 # 撮合假设
    slippage: str = "none"                      # 滑点模型
    #: **F9（2026-10-06）**：价内滑点成本披露（元 + 与显性费用倍数）。
    #: 由 `app.assemble_assumptions` 从 `bundle.fills` 的 `ref_price` 还原
    #: （`Σ qty×|price−ref|`）；空串 = 不披露（旧产物无 `ref_price`）。
    slippage_cost: str = ""
    fee_segments: tuple[dict[str, Any], ...] = ()   # 费率分段明细（H3）
    rules_version: str = "none"                 # H2 规则源版本指纹
    rules_overrides: tuple[dict[str, Any], ...] = ()
    degraded: tuple[str, ...] = ()              # 降级事件（采样/缺失/降级指标）
    notes: tuple[str, ...] = ()
    #: 口径披露（P1-NEW-5，19 号 §22.2）：宇宙/基准等**结论前提**——原
    #: `runtime.assembly_notes` 零消费者（只落日志），跨区间/跨实验不可比
    assembly: tuple[str, ...] = ()


def format_matching(name: str) -> str:
    """撮合处理器名 → 人类可读串（**RC-03，2026-10-05**）。

    报告「假设与披露」的假设字段**一律读生效配置**，不得硬编码——原实现把
    撮合写死为 `next_open（T+1 开盘撮合）`，用户在 `execution.handler` 换实现
    后报告仍显示旧值（披露与事实相反）。
    """
    labels = {"next_open": "next_open（T+1 开盘撮合）"}
    key = str(name)
    return labels.get(key, key)


def format_slippage(model: str, params: Mapping[str, Any] | None = None) -> str:
    """滑点模型 + 参数 → 人类可读串（**RC-03，2026-10-05**；13 §19.4）。

    约定：
        `none`  → `none（显式声明，零摩擦基线）`
        `pct`   → `pct 0.001（单边 10bp）`
        `fixed` → `fixed 0.01 元`
        其它模型 → 原样名前 + JSON 参数（未知实现也必须如实显示）
    """
    key = str(model)
    p = dict(params or {})
    if key == "none":
        return "none（显式声明，零摩擦基线）"
    if key == "pct":
        pct = p.get("pct", 0.001)
        return f"pct {pct:g}（单边 {pct * 10000:g}bp）"
    if key == "fixed":
        return f"fixed {p.get('amount', 0.01):g} 元"
    if key == "sqrt_impact":
        # RC-12（2026-10-05）：研究参数**必须可见**（η/σ/cap 全部显示）
        eta = p.get("eta", 0.1)
        sigma = p.get("sigma", 0.02)
        cap = p.get("impact_cap", 0.05)
        return (f"sqrt_impact η={eta:g} σ={sigma:g} "
                f"cap={cap * 10000:g}bp（规模相关冲击；研究参数）")
    if p:
        return f"{key} {json.dumps(p, ensure_ascii=False, sort_keys=True)}"
    return key


#: 强制章节（不可被 `report.sections` 关闭；13 §19.4 + BB-1）
_MANDATORY_SECTIONS = ("assumptions", "reproduce")

#: `report.sections`（文档语义名）→ 编排章节 id 的映射（P3-5 接线用）
_SECTION_ALIASES: dict[str, str] = {
    "summary": "summary",
    "equity": "performance", "drawdown": "performance",
    "monthly_heat": "performance", "metrics_table": "performance",
    "trades": "trading", "weights_area": "trading",
    "assumptions": "assumptions", "reproduce": "reproduce",
    "appendix": "appendix",
}


def _derived_disclosures(manifest: Any) -> tuple[str, ...]:
    """从**生效配置**推导must-披露项（19 号审查 R1-5 / P0-3 / P2-9 对称化）。

    CLI 与 api 两入口共享本构建器 → 披露天然对称、无分叉：
      - `execution.cost_model` 缺省/为 zero → 零费用基线（P1-6）
      - `risk.allow_empty_chain=true` → 风控裸奔调试模式（P0-3 逃生开关）
      - `data.allow_degraded_limit=true` → 涨跌停约束降级（P1-11 区间守卫）
    """
    cfg = dict(getattr(manifest, "config_effective", None) or {})
    out: list[str] = []
    exec_cfg = cfg.get("execution") or {}
    cost = exec_cfg.get("cost_model", "zero")
    if cost in ("zero", None):
        out.append("成本模型 = zero（未显式配置）：本报告为**零费用基线**，"
                   "实盘结论需按分段费率（tiered_v1）重估（19 号 P1-6）")
    risk_cfg = cfg.get("risk") or {}
    if risk_cfg.get("allow_empty_chain"):
        out.append("**风控链为空**（risk.allow_empty_chain=true）：本回测无任何"
                   "风控约束，仅供调试（19 号 P0-3）")
    data_cfg = cfg.get("data") or {}
    if data_cfg.get("allow_degraded_limit"):
        out.append("**涨跌停约束降级**（data.allow_degraded_limit=true）："
                   "区间早于 stk_limit 起点，涨跌停为无效世界（19 号 P1-11）")
    return tuple(out)


def _voided_banner(run_id: str) -> str | None:
    """**R36′（§95.3-A4；20 号纪律四「作废横幅」）**：本 run 是否已作废。

    判定源＝`回测产物/runs/作废登记-*.md`（审查方落盘；Q-8=(a) 裁定——
    既往受污染 run 全部标注作废）。命中 ⇒ 返回「原因 ＋ 重跑条件」文案，
    报告顶部渲染红色横幅（**不得静默**——被作废的结论不可当有效结果引用）。
    登记表缺失 / 未命中 ⇒ None（披露不得恒现）。
    """
    try:
        from btf.config import paths as _paths

        runs_dir = pathlib.Path(_paths.RUNS_DIR)
    except Exception:                     # 配置不可用（纯单元场景）
        return None
    if not runs_dir.is_dir():
        return None
    for reg in sorted(runs_dir.glob("作废登记-*.md")):
        try:
            text = reg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if run_id not in text:
            continue
        row = next((ln.strip() for ln in text.splitlines()
                    if run_id in ln and ln.strip().startswith("|")), "")
        cells = [c.strip() for c in row.strip("|").split("|")] if row else []
        reason = cells[-1] if cells else "见登记表"
        return (f"本 run 已作废：{reason}；重跑条件＝修复后重跑（先执行 "
                f"`bt data scan-consolidation` 确认无遗漏折算）；"
                f"登记源：{reg.name}（19 号 §95.2–§95.3）")
    return None


_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_CODE = re.compile(r"`([^`]+)`")


def _md_lite(text: str) -> str:
    """**R40′（N42，2026-10-08）**：横幅文案的**轻量 Markdown 渲染**。

    作废登记表（`回测产物/runs/作废登记-*.md`）的原因单元格含 `**粗体**`
    与 `` `行内代码` ``——原样进 Jinja（autoescape）会显示为字符（DOM 级
    负向对照 N42）。先 `html.escape`（防注入）再替换，仅支持两式；
    登记表纯文本仍可读（渲染不依赖登记表格式）。
    """
    out = html.escape(text)
    out = _MD_BOLD.sub(r"<strong>\1</strong>", out)
    out = _MD_CODE.sub(r"<code>\1</code>", out)
    return out


def _assembly_disclosures(manifest: Any) -> tuple[str, ...]:
    """口径披露（P1-NEW-5）：装配期笔记 + 宇宙/基准口径。

    `runtime.assembly_notes`（如 `source=all` 期间并集"跨期新增 N 只"）原
    **零消费者**——只经 logger 落控制台、不进产物；而宇宙口径是回测结论的
    **前提**（5,690 只机会集与 5,067 只不是同一个结论），故与 `degraded`
    同通道进报告（19 号 §22.2「两套披露机制待遇不对称」）。
    """
    cfg = dict(getattr(manifest, "config_effective", None) or {})
    out = list(getattr(manifest, "assembly_notes", ()) or ())
    universe_src = ((cfg.get("run") or {}).get("universe") or {}).get("source")
    if universe_src:
        out.append(f"标的宇宙口径：`run.universe.source={universe_src}`"
                   f"（装配期解析结果见上；跨区间比较须核对同一机会集）")
    # MM-6（19 号 §137 B）：多基准口径——首个＝主基准（进 metrics），其余
    # 只进披露区（`report.benchmarks[]`），此处显式点名以免"算了却没披露"。
    from btf.config.loader import benchmark_symbols

    benchmarks = benchmark_symbols(cfg.get("report") or {})
    if benchmarks:
        text = (f"基准对比：`{benchmarks[0]}`（**主基准**——相对指标取自同期"
                f"指数收盘价，缺日 fail-closed，不前向填充；19 号 R2.5/P1-10）")
        if len(benchmarks) > 1:
            others = "、".join(f"`{s}`" for s in benchmarks[1:])
            text += (f"；对照基准 {others}——**只进披露区**（不写入 metrics，"
                     f"P0-NEW-7/MM-6）")
        out.append(text)
    else:
        # RC-09（2026-10-05）：基准**软强制**的披露半边——不配不阻断（保持
        # 灵活），但「无基准对照」必须显式标注：IR/超额等相对指标不存在，
        # 不得以绝对收益替代基准结论（04 §8.6 / 17 术语表）。
        out.append("**无基准对照**（`report.benchmark` 未配置）：IR / 超额等"
                   "相对指标不存在——不得以绝对收益替代基准结论（RC-09）")
    return tuple(out)


def _benchmark_table(manifest: Any) -> str:
    """多基准对照表（**MM-6**，19 号 §137 B 验收 ③）：≥2 个基准才渲染。

    数据取 `manifest.benchmarks`（run 期落盘，`report.benchmarks[]`）——本层
    **不重算**（铁律 5 禁 viz → analytics；且报告须与落盘同源，不得二次口径）。
    单基准 ⇒ 空串（不新增章节；既有报告结构零变化）。
    """
    rows = list(getattr(manifest, "benchmarks", ()) or ())
    if len(rows) < 2:
        return ""
    header = ["基准", "角色", "基准总收益", "超额收益", "年化超额", "跟踪误差",
              "信息比率", "beta", "alpha（年化）", "上行捕获", "下行捕获"]
    body = []
    for row in rows:
        row = dict(row)
        body.append([
            row.get("symbol", "—"),
            "主基准（进 metrics）" if row.get("primary") else "对照（仅披露）",
            _fmt(row.get("benchmark_total_return")),
            _fmt(row.get("excess_return")),
            _fmt(row.get("annualized_excess")),
            _fmt(row.get("tracking_error")),
            _fmt(row.get("information_ratio")),
            _fmt(row.get("beta")),
            _fmt(row.get("alpha_annualized")),
            _fmt(row.get("up_capture")),
            _fmt(row.get("down_capture")),
        ])
    return _table(body, header)



#: 报告模板。**转义纪律（Z-1 / 19 号 §26.2 / 铁律新 14）**：
#: `Environment(autoescape=True)` 下，**已构建好的 HTML 片段**（`_cards` /
#: `_charts_html` / `_table` / `_CSS` 的输出）必须显式 `| safe`——否则整段正文
#: 会被转义成纯文本，报告在视觉上完全不可用（六章节卡表失形、**六图全灭**；
#: 该缺陷自报告功能诞生起存在，五轮审查因"文本级断言看不见标签"而全漏）。
#: 纯文本字段（title / period / manifest.* 等）**保持默认转义**，不得加 `| safe`。
_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{{ title }}</title>
<style>{{ css | safe }}</style></head>
<body>
<header class="cover">
  <h1>{{ title }}</h1>
  <p class="sub">run_id <code>{{ manifest.run_id }}</code> · 状态 {{ manifest.status }}</p>
  <table class="kv">
    <tr><th>区间</th><td>{{ period }}</td></tr>
    <tr><th>数据版本</th><td>{{ data_version }}</td></tr>
    <tr><th>代码版本</th><td>{{ code_version }}</td></tr>
    <tr><th>配置哈希</th><td><code>{{ manifest.config_hash }}</code></td></tr>
    <tr><th>环境</th><td>{{ env_version }}</td></tr>
    <tr><th>契约版本</th><td>{{ manifest.contract_version }}</td></tr>
  </table>
</header>
{% if voided %}<div class="void-banner">⚠ {{ voided | safe }}</div>{% endif %}
{% for section in sections %}
<section id="{{ section.id }}">
  <h2>{{ section.title }}</h2>
  {{ section.html | safe }}
</section>
{% endfor %}
<footer><p>由 btf ReportBuilder 生成 · 图表后端 {{ backend }}</p></footer>
</body></html>
"""

_CSS = """
body{font-family:"Microsoft YaHei",system-ui,sans-serif;margin:2rem;color:#1a202c}
h1{font-size:1.6rem}h2{border-bottom:2px solid #2b6cb0;padding-bottom:.3rem;margin-top:2rem}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{border:1px solid #e2e8f0;padding:.3rem .5rem;text-align:right}
th:first-child,td:first-child{text-align:left}
code{background:#edf2f7;padding:.1rem .3rem}
.cards{display:flex;gap:1rem;flex-wrap:wrap}
.card{border:1px solid #e2e8f0;border-radius:.5rem;padding:.8rem 1.2rem;min-width:10rem}
.card .k{font-size:.8rem;color:#4a5568}.card .v{font-size:1.3rem;font-weight:600}
.note{color:#718096;font-size:.85rem}
.sparkline{width:100%;height:120px;border:1px solid #e2e8f0}
.missing{color:#c53030;font-size:.9rem}
.void-banner{background:#fff5f5;border:2px solid #c53030;color:#c53030;
  padding:.8rem 1rem;margin:1rem 0;border-radius:.4rem;font-weight:600}
"""


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and value != value:
        return "—"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def _cards(metrics: dict[str, float]) -> str:
    keys = [("期末资产", "final_nav"), ("累计收益", "total_return"),
            ("年化收益", "annualized_return"), ("夏普", "sharpe_ratio"),
            ("最大回撤", "max_drawdown"), ("成交笔数", "n_fills")]
    items = "".join(
        f"<div class='card'><div class='k'>{label}</div>"
        f"<div class='v'>{_fmt(metrics.get(key), 4)}</div></div>"
        for label, key in keys)
    return f"<div class='cards'>{items}</div>"


def _table(rows: list[list[Any]], header: list[str]) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>"
                   for row in rows)
    return (f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")


def _esc(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


class ReportBuilder:
    """报告编排器（13 §19.1 编排层：只排章节与模板，不含绘图逻辑）。"""

    def __init__(self, bundle: RunResultView, assumptions: Assumptions | None = None,
                 charts: tuple[str, ...] = DEFAULT_CHARTS,
                 title: str | None = None):
        self.bundle = bundle
        # 派生披露在构造期**一次性**并入（19 号 R1-5/P0-3）——放 build() 内
        # 会随重复构建累积（幂等破坏，实测踩过）；CLI 与 api 共享本构建器
        # 故披露对称、零分叉
        from dataclasses import replace as _replace

        base = assumptions or Assumptions()
        self.assumptions = _replace(
            base,
            degraded=tuple(base.degraded)
            + _derived_disclosures(bundle.manifest),
            assembly=tuple(base.assembly)
            + _assembly_disclosures(bundle.manifest))
        self.charts = tuple(charts)
        self.title = title or f"回测报告 · {bundle.run_id}"
        # **R36′（§95.3-A4）**：作废横幅（命中作废登记表 ⇒ 报告顶部红条，
        # 构造期一次性求值——同 `_assembly_disclosures` 的幂等纪律）。
        self.voided_banner = _voided_banner(str(bundle.run_id))
        self._plotlyjs_done = False      # plotly.js 全报告只内联一次（自包含）

    # ── 构建 ──
    def build(self, *, include_assumptions: bool = True) -> str:
        self._plotlyjs_done = False      # 幂等：每次构建重置（同输入同输出）
        if not include_assumptions:
            raise ReportError(
                "假设与披露为强制章节（13 §19.4）——不可关闭"
                "（无此章节的报告视为不可信）")
        if Environment is None:
            raise ReportError("jinja2 不可用（viz extra 未安装）")

        data = self._chart_data()
        sections: list[dict[str, str]] = [
            {"id": "summary", "title": "执行摘要", "html": _cards(self.bundle.metrics)},
            {"id": "performance", "title": "绩效分析",
             "html": self._charts_html(data, ("equity", "drawdown",
                                              "monthly_heatmap", "metrics_table"))},
            {"id": "trading", "title": "交易分析",
             "html": self._charts_html(data, ("trades_table", "weights_area"))
                    + self._trading_stats()},
            {"id": "assumptions", "title": "假设与披露（强制章节）",
             "html": self._assumptions_html()},
            {"id": "reproduce", "title": "复现说明", "html": self._reproduce_html()},
            {"id": "appendix", "title": "附录：生效配置（脱敏）",
             "html": "<pre>" + _esc(json.dumps(
                 dict(self.bundle.manifest.config_effective), ensure_ascii=False,
                 indent=2, sort_keys=True)) + "</pre>"},
        ]

        # `report.sections` 接线（19 号 P3-5：原为死配置键——配置无效且不报错）
        # 配置使用的是**图表/文档语义名**（equity/drawdown/monthly_heat/trades…），
        # 章节 id 是编排名（performance/trading…）——经别名表映射
        wanted = (((self.bundle.manifest.config_effective or {})
                   .get("report") or {}).get("sections"))
        if wanted:
            ids: list[str] = []
            for name in wanted:
                mapped = _SECTION_ALIASES.get(name)
                if mapped and mapped not in ids:
                    ids.append(mapped)
            kept = [s for s in sections if s["id"] in ids]
            kept.sort(key=lambda s: ids.index(s["id"]))
            # **强制章节**（不可被 `report.sections` 关闭）：
            #   assumptions（假设与披露）——13 §19.4 明示"无此章节视为不可信"；
            #   reproduce（复现说明）——承载 metrics_digest / **数据指纹** /
            #   代码版本 / 种子（BB-1 验收：指纹须在报告可见，铁律新 16）；
            #   原实现只强制 assumptions，且 DEFAULTS 的默认章节表不含
            #   reproduce/appendix → **默认报告静默丢掉复现说明**（本轮实测：
            #   真实 CLI 产物仅有 4 章；该缺陷使"数据指纹"无处可见）。
            forced = [s for s in sections if s["id"] in _MANDATORY_SECTIONS]
            sections = kept + [s for s in forced if s not in kept]

        # **MM-6（19 号 §137 B 验收 ③）**：多基准对照表——≥2 个基准才出现；
        # 置于强制章节之前（**不受 `report.sections` 过滤**：对照基准的相对
        # 指标只存在于本表，被过滤即等于"算了不披露"）
        benchmark_html = _benchmark_table(self.bundle.manifest)
        if benchmark_html:
            sections.insert(1, {"id": "benchmark_comparison",
                                "title": "基准对照（多基准）",
                                "html": benchmark_html})

        env = Environment(autoescape=True, undefined=StrictUndefined)
        template = env.from_string(_TEMPLATE)
        manifest = self.bundle.manifest
        return template.render(
            title=self.title, manifest=manifest, css=_CSS, sections=sections,
            # R40′（N42）：轻量 MD 渲染（粗体/行内代码）；空 ⇒ 不渲染横幅
            voided=(_md_lite(self.voided_banner) if self.voided_banner
                    else None),
            period=self._period(), data_version=self._data_version(),
            code_version=f"{manifest.code_version.get('package_version', '—')}"
                         f" @ {manifest.code_version.get('git_commit', '—')}"
                         f"{'(dirty)' if manifest.code_version.get('dirty') else ''}",
            env_version=f"python {manifest.env_version.get('python', '—')}"
                        f" / {manifest.env_version.get('platform', '—')}",
            backend="plotly" if plotly_available() else "内置降级渲染",
        )

    # ── 章节片段 ──
    def _chart_data(self) -> dict[str, Any]:
        return {
            "snapshots": self.bundle.snapshots,
            "trades": self.bundle.trades,
            "fills": self.bundle.fills,
            "rejections": self.bundle.rejections,
            "metrics": self.bundle.metrics,
        }

    def _charts_html(self, data: dict[str, Any], names: tuple[str, ...]) -> str:
        parts: list[str] = []
        for name in [n for n in self.charts if n in names]:
            try:
                spec = build_chart(name, data)
            except (ValueError, KeyError, IndexError) as exc:
                parts.append(
                    f"<p class='missing'>图表 {_esc(name)} 缺失：{_esc(exc)}"
                    f"（13 §19.6 降级：跳过不中断）</p>")
                continue
            include = not self._plotlyjs_done
            self._plotlyjs_done = True
            parts.append(render_html(spec, include_plotlyjs=include))
        return "".join(parts)

    def _trading_stats(self) -> str:
        metrics = self.bundle.metrics
        rows = [["成交笔数", _fmt(metrics.get("n_fills"), 0)],
                ["总成交额（元）", _fmt(metrics.get("total_turnover"), 2)],
                ["年化换手", _fmt(metrics.get("turnover_annualized"), 4)],
                ["总费用（元）", _fmt(metrics.get("total_fees"), 2)],
                ["费用占比", _fmt(metrics.get("fee_ratio"), 6)]]
        html = "<h3>换手与费用</h3>" + _table(rows, ["项目", "数值"])
        counts: dict[str, int] = {}
        for rej in self.bundle.rejections:
            counts[rej.get("code", "unknown")] = counts.get(
                rej.get("code", "unknown"), 0) + 1
        if counts:
            html += "<h3>拒单分布</h3>" + _table(
                [[code, n] for code, n in sorted(counts.items())], ["拒单码", "次数"])
        else:
            html += "<p class='note'>无拒单记录</p>"
        return html

    def _assumptions_html(self) -> str:
        a = self.assumptions
        segments = list(a.fee_segments)
        if segments:
            seg_html = _table(
                [[s.get("effective_from") or "（无限早）",
                  _fmt(s.get("stamp_duty_sell"), 5),
                  _fmt(s.get("transfer_fee_rate"), 6),
                  s.get("transfer_scope", "both"), s.get("transfer_basis", "amount")]
                 for s in segments],
                ["生效起始", "印花税（卖出）", "过户费率", "适用范围", "计费基数"])
        else:
            seg_html = ("<p class='missing'>费率分段明细未披露"
                        "（调用方未注入——不可信报告）</p>")
        overrides = list(a.rules_overrides)
        rules_html = (
            f"<p>规则源版本：<code>{_esc(a.rules_version)}</code></p>"
            + (_table([[json.dumps(o, ensure_ascii=False, sort_keys=True)]
                       for o in overrides], ["override 清单"])
               if overrides else "<p class='note'>无 override</p>"))
        degraded = list(a.degraded)
        degraded_html = (_table([[d] for d in degraded], ["降级事件"])
                         if degraded else "<p class='note'>无降级事件</p>")
        assembly = list(a.assembly)
        assembly_html = (_table([[x] for x in assembly], ["口径与装配披露"])
                         if assembly else "<p class='note'>无口径披露项</p>")
        cost_rows = [["撮合假设", a.matching], ["滑点模型", a.slippage]]
        if a.slippage_cost:
            # F9（2026-10-06）：价内滑点成本单列（修正 fee_ratio 低估真实成本）
            cost_rows.append(["滑点成本（价内）", a.slippage_cost])
        return (
            "<h3>撮合与成本假设</h3>"
            + _table(cost_rows, ["项目", "取值"])
            + "<h3>口径与装配披露（P1-NEW-5）</h3>" + assembly_html
            + "<h3>费率分段明细（H3）</h3>" + seg_html
            + "<h3>规则版本（H2）</h3>" + rules_html
            + "<h3>降级事件</h3>" + degraded_html
            + ("".join(f"<p class='note'>{_esc(n)}</p>" for n in a.notes))
        )

    def _reproduce_html(self) -> str:
        m = self.bundle.manifest
        rows = [["重跑命令", f"bt run --config <config.yaml>  # run_id={m.run_id}"],
                ["config_hash", m.config_hash],
                ["metrics_digest", m.metrics_digest or "—"],
                ["数据指纹", str(m.data_version.get("content_hash", "—"))],
                ["代码版本", str(m.code_version.get("git_commit", "—"))],
                ["种子", str(m.seed.get("master", 0))]]
        return _table(rows, ["项目", "值"])

    def _period(self) -> str:
        snaps = self.bundle.snapshots
        if not snaps:
            return "—"
        return f"{snaps[0].date} ~ {snaps[-1].date}（{len(snaps)} 个交易日）"

    def _data_version(self) -> str:
        dv = self.bundle.manifest.data_version
        return (f"{dv.get('dataset', '—')} @ {dv.get('anchor_date') or '—'}"
                f" · {dv.get('completeness_level', '—')}")


__all__ = ["Assumptions", "ReportBuilder", "ReportError", "RunResultView",
           "format_matching", "format_slippage"]
