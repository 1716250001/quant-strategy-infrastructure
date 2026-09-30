# -*- coding: utf-8 -*-
"""
tools/gen_db_report.py — 数据库结构报告生成器
==============================================
从 `db_schema.json`（机器可读）生成人类可读的 Markdown 结构报告。

为什么要有这个工具
------------------
结构报告此前是一次性人工写的，库一旦发生布局调整（如 2026-09-17 全库
by_code → by_year 迁移）报告就整篇失真，且修补 600+ 行不现实。
改为从 schema JSON 生成：数据部分永远与库一致，只维护模板。

用法:
  python main.py db-report                 # 生成到 文档/数据与工程/
  python main.py db-report --out PATH      # 指定输出
  python -m tools.gen_db_report
"""
import os
import json
import argparse
from datetime import datetime

from config import (MARKET_DATA_DIR, DOC_DIR, DB_AUDIT_PKEYS, TABLE_DATE_COL,
                    DAILY_UPDATE_GROUPS)

SCHEMA_PATH = os.path.join(DOC_DIR, "数据与工程", "db_schema.json")
OUT_DIR = os.path.join(DOC_DIR, "数据与工程")

# 类别展示顺序（需与 db_schema.py 里写入的 category 取值一致）
CATEGORY_ORDER = ["元数据", "行情", "资金", "财务", "事件", "跨品种",
                  "指数衍生", "宏观", "其他"]


def _fmt(n):
    return f"{n:,}" if isinstance(n, int) else str(n)


def _category_rank(cat):
    try:
        return CATEGORY_ORDER.index(cat)
    except ValueError:
        return len(CATEGORY_ORDER)


def _table_row(name, t):
    pk = t.get("pk")
    pk_s = "-" if not pk else " ".join(f"`{c}`" for c in pk)
    dr = ""
    if t.get("date_min") or t.get("date_max"):
        dr = f"{t.get('date_min') or '-'}~{t.get('date_max') or '-'}"
    return (f"| `{name}` | {t.get('description','')} | {t.get('layout','')} | "
            f"{_fmt(t.get('files',0))} | {_fmt(t.get('rows',0))} | "
            f"{t.get('size_mb',0):.1f}MB | {t.get('n_columns',0)} | "
            f"`{t.get('date_col','')}` | {dr} | {pk_s} |")


def build_markdown(d):
    today = datetime.now().strftime("%Y-%m-%d")
    tables = d["tables"]
    # ⚠ 2026-09-22 修复（P1-6）: 布局表数必须**动态统计**，不得手写。
    #   原硬编码"56 张表"已过时（随布局迁移持续变化，实测当前 schema 为 58）。
    n_by_year = sum(1 for t in tables.values() if t.get("layout") == "by_year")
    n_single = sum(1 for t in tables.values() if t.get("layout") == "single")
    lines = []

    # ── 头部 ──
    lines += [
        "# A股量化数据库 · 完整结构报告",
        "",
        f"> 生成时间：{d['generated_at']}　|　库根：`{d['root']}`",
        "> 本报告由 `python main.py db-report` 从 `db_schema.json` 自动生成 —— "
        "数据部分与库实时一致，请勿手工修改。",
        "",
        "---",
        "",
        "## 一、总览",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 表（目录）数 | {d['total_dirs']} |",
        f"| parquet 文件数 | {_fmt(d['total_files'])} |",
        f"| 总行数 | {_fmt(d['total_rows'])} |",
        f"| 总体积 | {d['total_gb']} GB |",
        f"| 存储根目录 | `{d['root']}` |",
        "",
    ]

    # 按类别分布
    by_cat = {}
    for name, t in tables.items():
        cat = t.get("category") or "其他"
        c = by_cat.setdefault(cat, {"n": 0, "rows": 0, "mb": 0.0, "files": 0})
        c["n"] += 1
        c["rows"] += t.get("rows", 0)
        c["mb"] += t.get("size_mb", 0.0)
        c["files"] += t.get("files", 0)

    lines += ["### 按类别分布", "",
              "| 类别 | 表数 | 文件数 | 行数 | 体积 |",
              "|------|------|--------|------|------|"]
    for cat in sorted(by_cat, key=_category_rank):
        c = by_cat[cat]
        lines.append(f"| {cat} | {c['n']} | {_fmt(c['files'])} | "
                     f"{_fmt(c['rows'])} | {c['mb']/1024:.2f} GB |")
    lines.append("")

    # ── 快速上手 ──
    lines += [
        "---",
        "",
        "## 二、快速上手",
        "",
        "### 2.1 推荐读法（布局无关，永远用它）",
        "",
        "```python",
        "from common import reader",
        "",
        '# 读单只标的一段区间（自动适配按年分区）',
        'df = reader.read_code("daily", "600519.SH",',
        '                      start_date="20260101", end_date="20260917")',
        "",
        '# 读区间全市场（截面扫描/回测；by_year 下顺序读年度文件，很快）',
        'df = reader.read_range("moneyflow", start_date="20260901")',
        "",
        '# 代码清单',
        'codes = reader.list_codes("daily")            # 元数据全集（含退市）',
        'codes = reader.codes_from_store("daily")      # 库里真有数据的',
        "",
        '# 该表最新日期',
        'latest = reader.latest_date("daily")',
        "```",
        "",
        "### 2.2 布局与路径模板（AI 必须遵守）",
        "",
        "全库已统一为 **按年分区**，路径模板只有两种：",
        "",
        "| 布局 | 路径模板 | 说明 |",
        "|------|----------|------|",
        "| `by_year` | `{ROOT}\\<表名>\\<YYYY>.parquet` | **默认布局**，"
        + str(n_by_year) + " 张表 |",
        "| `by_code` | `{ROOT}\\<表名>\\<ts_code>.parquet` | 仅 `stock_company` |",
        "| `single` | `{ROOT}\\<表名>\\<表名>.parquet` | 一次性全量的表，"
        + str(n_single) + " 张 |",
        "",
        "> ⚠ **不要**按 `{表}\\{ts_code}.parquet` 硬编码拼路径：按年分区下文件名是"
        "年份，`listdir` 会把 `\"2026\"` 当成证券代码。",
        "",
        "### 2.3 跨表关联键",
        "",
        "| 键 | 说明 |",
        "|----|------|",
        "| `ts_code` | 统一证券代码：`600519.SH` / `000001.SZ` / `510050.SH` / "
        "`113042.SH`(可转债) / `000001.OF`(场外基金) / `XAUUSD.FXCM`(外汇) |",
        "| `trade_date` | 交易日 `YYYYMMDD`（字符串） |",
        "| `end_date` | 报告期 `YYYYMMDD`（财务类） |",
        "| `nav_date` | 净值日期（`fund_nav`） |",
        "| `ann_date` | 公告日（事件类） |",
        "",
        f"各表的分区列单一真源：`config.TABLE_DATE_COL`。",
        "",
    ]

    # ── 存储布局规范 ──
    lines += [
        "---",
        "",
        "## 三、存储布局规范",
        "",
        "### 为什么统一用 `by_year`",
        "",
        "`by_code`（一标的一文件）下，每日增量要给数千个文件各做一次 "
        "`read→concat→dedup→write`：",
        "",
        "| 表 | 旧文件数 | 单日落盘成本 |",
        "|----|----------|--------------|",
        "| `daily` | 5,903 | ≈111 秒 |",
        "| `index_daily` | 10,849 | ≈200 秒 |",
        "| `fund_nav` | 20,775 | —— |",
        "| 合计 | —— | **约 8.3 分钟/天** |",
        "",
        "`by_year` 下每天只改 1 个年度文件，落盘降到毫秒级。",
        "",
        "### 迁移结果（2026-09-17）",
        "",
        "| 指标 | 迁移前 | 迁移后 |",
        "|------|--------|--------|",
        "| 文件数 | 154,069 | 6,850 |",
        "| 体积 | 8.73 GB | 4.79 GB |",
        "",
        "> 体积下降主要来自小文件元数据/压缩块开销的消除，而非数据减少。"
        "迁移时仅做**全列判重**（`stk_holdertrade` 等表源数据本身含整行重复）。",
        "",
        "### 唯一例外",
        "",
        "`stock_company`（一标的一行的静态元数据、无任何日期列）保持 `by_code`。"
        "按年分区对它无意义，且会导致 `read_code` 传日期参数时静默读空。",
        "",
    ]

    # ── 逐表明细 ──
    lines += ["---", "", "## 四、逐表结构明细", ""]
    for cat in sorted(by_cat, key=_category_rank):
        members = [(n, t) for n, t in tables.items()
                   if (t.get("category") or "其他") == cat]
        members.sort(key=lambda x: -x[1].get("rows", 0))
        c = by_cat[cat]
        lines += [
            f"### 4.{_category_rank(cat)+1} {cat}"
            f"（{len(members)} 表 / {_fmt(c['rows'])} 行 / {c['mb']/1024:.2f} GB）",
            "",
            "| 表 | 说明 | 布局 | 文件 | 行数 | 大小 | 列数 | 日期列 | 范围 | 主键 |",
            "|----|------|------|------|------|------|------|--------|------|------|",
        ]
        for n, t in members:
            lines.append(_table_row(n, t))
        lines.append("")

    # ── 主键表 ──
    lines += [
        "---",
        "",
        "## 五、主键配置（体检/清理用）",
        "",
        "`config.DB_AUDIT_PKEYS` —— 用于 `db-audit` 判断目录内是否有真重复。",
        "**主键按「单文件内能否唯一标识一行」定义**：按年分区的大表必须含标的列，"
        "否则同一天全市场都会被判为重复。",
        "",
        "| 表 | 主键 |",
        "|----|------|",
    ]
    for n in sorted(DB_AUDIT_PKEYS):
        pk = DB_AUDIT_PKEYS[n]
        lines.append(f"| `{n}` | {'- (跳过重复检查)' if not pk else ' '.join(f'`{c}`' for c in pk)} |")
    lines.append("")

    # ── 每日更新 ──
    # ⚠ 2026-09-22 修复（P1-6）: 接口清单必须**从 config 派生**，不得手写。
    #   原实现硬编码 extend 12 张，而 config 实为 32 张 → 漏 20 张；
    #   该报告自我定位"数据部分永远与库一致"，手写清单必然漂移
    #   （同族事故：定时任务 prompt 手写清单曾导致 us_daily 长期漏跑）。
    grp_rows = [f"| {g}（{len(ts)} 张） | " + " / ".join(ts) + " |"
                for g, ts in DAILY_UPDATE_GROUPS.items()]
    lines += [
        "---",
        "",
        "## 六、每日更新涉及的接口",
        "",
        "`python main.py gap-update` 默认跑 `core` 组（下游策略直接咬合），"
        "`--group all` 追加 `extend` 组。下表**由 `config.DAILY_UPDATE_GROUPS` 自动生成**，"
        "请勿手工维护。",
        "",
        "| 组 | 接口 |",
        "|----|------|",
        *grp_rows,
        "",
        "**复核窗口**：除补缺口外，每日强制重拉最近 3 个交易日"
        "（`config.DAILY_UPDATE_LOOKBACK`），用于修复「断点已记但当日数据残缺」"
        "（实测 `daily_basic` 2026-09-16 仅入库 130 行，而缺口算法看不到）。"
        "落盘走主键去重，重拉幂等。",
        "",
    ]

    return "\n".join(lines)


def run(out=None, verbose=True, as_json=False, refresh=False):
    """生成结构报告。

    refresh=True: 先扫描刷新 schema 再出报告（= db-schema + db-report 一步完成）
    as_json=True: stdout 输出回执 JSON（Markdown 文件照写；CLI --json）
    """
    if refresh:
        from tools.db_schema import run_schema
        run_schema(verbose=False, quiet=True, announce=False)

    if not os.path.exists(SCHEMA_PATH):
        if as_json:
            from common.jsonio import print_json
            print_json({"error": f"缺少 {SCHEMA_PATH}",
                        "hint": "先运行 python main.py db schema"})
        else:
            print(f"  [ERROR] 缺少 {SCHEMA_PATH}，请先运行 python main.py db-schema")
        return None
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        d = json.load(f)

    md = build_markdown(d)
    fname = f"数据库结构报告_{datetime.now().strftime('%Y%m%d')}.md"
    path = out or os.path.join(OUT_DIR, fname)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)

    if as_json:
        from common.jsonio import print_json
        print_json({"out": path, "schema": SCHEMA_PATH,
                    "tables": d["total_dirs"], "total_files": d["total_files"],
                    "total_rows": d["total_rows"], "total_gb": d["total_gb"],
                    "kb": round(len(md) / 1024), "lines": md.count(chr(10))})
    elif verbose:
        print(f"  表数={d['total_dirs']} 文件={d['total_files']:,} "
              f"行={d['total_rows']:,} 体积={d['total_gb']}GB")
        print(f"  已生成: {path}  ({len(md)/1024:.0f} KB / {md.count(chr(10))} 行)")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="数据库结构报告生成器")
    ap.add_argument("--out", default=None, help="输出路径")
    ap.add_argument("--refresh", action="store_true",
                    help="先扫描刷新 schema 再生成报告（= db-schema + db-report 一步完成）")
    ap.add_argument("--json", action="store_true", help="stdout 输出回执 JSON（文件照写）")
    a = ap.parse_args(argv)
    run(out=a.out, as_json=a.json, refresh=a.refresh)


if __name__ == "__main__":
    main()
