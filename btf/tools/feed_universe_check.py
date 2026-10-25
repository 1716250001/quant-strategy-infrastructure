# -*- coding: utf-8 -*-
"""feed_universe_check.py —— Feed × 标的路由预检（P17 前置门禁，只读）

为什么需要（P17，2026-10-22 实测）
----------------------------------
`data.feed: "tushare_parquet"` 的 `bars_of()` **默认 `daily_table="daily"`**
（`btf/data/feed.py:481-487`，`TushareParquetFeed`）。该 Feed 的设计是「由调用方
声明日线表」（docstring `feed.py:305-312`：ETF 场景传 `fund_daily`），但 **engine /
runtime 从不传这个参数** ⇒ 宇宙里只要有 ETF/LOF 或指数标的，读价就**全程拿到 0 根
bar**，而 run 仍会 `status=COMPLETED`、`exit=0`、`verify` digest 一致、**零告警**。

实测（本区，2026-10-22）：
    feed=tushare_parquet + universe.explicit=[510210.SH] → n_days=0 / n_fills=0 / COMPLETED
    feed=mixed（按代码段路由）                          → 13.3 s 跑完，final_nav=1,200,700.2392
    bars_of 计数（2021-09-01~2021-12-31，source=tushare_local，as_of=2026-10-09 镜像）：
        510210.SH  parquet=0   mixed=81
        600519.SH  parquet=81  mixed=81
        000300.SH  parquet=0   mixed=81
        159915.SZ  parquet=0   mixed=81
        920627.BJ  parquet=57  mixed=57

**为什么既有数据质量门（CC-1..CC-5）没拦住**：`run_data_quality` 的 `price_tables`
由标的类别直接拼表名并**直读 parquet**（`btf/runtime/component_assembler.py:136`），
不经 `feed.bars_of` ⇒ 质量门读 `fund_daily` 有数据、判「全部通过」，而 feed 读的是
`daily`（0 行）。同一处 `data_tables()`（`component_assembler.py:71-82`）还把
`fund_daily` 写进数据指纹——**指纹覆盖的表与 feed 实际读的表不是同一张**。

本工具做什么
------------
1. 读配置的 `data.feed` + `run.universe`，枚举宇宙（`explicit` / `index`；`all` 显式 SKIP）；
2. 对每个标的用**平台真源** `btf.data.tables_meta.daily_table_of()` 求路由表，
   与 **Feed 能力表**（下节，逐条附代码行号）比对，逐个判 OK / MISMATCH / SKIP；
3. `--probe`（可选）真起 Feed 数 bar：路由判 OK 但 0 根 bar 记 `ZERO_BARS`
   （P17 的表症；也可能是区间外/未上市，故必须人判）；
4. 不写任何文件、不改任何 run 产物、不改 btf 核心。

Feed 能力表（**以代码为准，不猜**）
----------------------------------
| feed              | 支持的日线表                              | 依据 |
|-------------------|-------------------------------------------|------|
| `tushare_parquet` | `daily` **仅此一张**                      | `feed.py:481-487` `bars_of(..., daily_table: str = "daily")`；`feed.py:524-530` `bars(...)` 同默认；docstring `feed.py:309-311` 明确「调用方声明」，而 engine/runtime 无任何调用点传 `daily_table`（全仓 grep 仅 `feed.py` 自身命中参数名） |
| `mixed`           | `daily` + `fund_daily` + `index_daily`    | `feed.py:791` `groups.setdefault(daily_table_of(sym), …)`；`feed.py:819` `bars_of(..., daily_table=daily_table or daily_table_of(symbol))`；`feed.py:861-865` `bars()` 按 `daily_table_of` 逐表归并（三表同构列 `feed.py:734-736`） |

> `mixed` 支持 `index_daily` 是**代码事实 + 实测**（000300.SH 2021Q4 = 81 根 bar），
> 不是推断；`btf/engine/loop.py:292` 亦对 `daily_table_of(...)=="index_daily"` 单独
> 处理复权语义（指数原样返回）。
>
> 两个 Feed 名即平台注册表全量（`btf/registry/catalog.py:65-66`）；其他名字 → exit 2。

用法
----
    python -X utf8 tools/feed_universe_check.py --config <yaml|yml|json>
    python -X utf8 tools/feed_universe_check.py --config <yaml> --feed mixed   # 覆盖 data.feed（试跑对照）
    python -X utf8 tools/feed_universe_check.py --root <dir>                   # 递归扫配置（*.yaml/*.yml + manifest.json）
    python -X utf8 tools/feed_universe_check.py --config <yaml> --probe        # 真起 Feed 数 bar
    python -X utf8 tools/feed_universe_check.py --config <yaml> --json
    python -X utf8 tools/feed_universe_check.py --selftest

退出码
------
    0 = 全部 OK（且至少判定了 1 个标的）
    1 = 有 MISMATCH（`--probe` 下 `ZERO_BARS` 同样计为不通过）；或 `--selftest` 失败
    2 = 环境/输入错误；**或全部 SKIP（无可判定标的 ⇒ fail-closed，不当通过）**

（对齐 AGENTS「校验三原则」：exit==0 才可报通过、内置负向测试、SKIP/未命中项逐条显式列出）

已验证双向用例（2026-10-22 实测，命令均带 `PYTHONIOENCODING=utf-8` + `-X utf8`）
----------------------------------------------------------------
> 夹具位置说明：任务书原本指定 `_etf_diag/etf_510210.yaml`（当时 `feed: mixed`），但该文件
> 在 **12:09:33 被外部改回 `tushare_parquet`**（父会话正在用它做 P17 反例）。为不改动他人
> 工作文件，夹具复制到 `handoff-20261022/E-fixtures/`，两条命令如下：

    1) 正例 `E-fixtures/etf_510210_mixed.yaml`（`data.feed: mixed`）：
       python -X utf8 tools/feed_universe_check.py \
         --config "<WS>/.context/btf/handoff-20261022/E-fixtures/etf_510210_mixed.yaml"
       → `510210.SH → fund_daily`，verdict=OK，**exit=0**

    2) 负例 `E-fixtures/etf_510210_parquet.yaml`（同内容，`data.feed: tushare_parquet`）：
       python -X utf8 tools/feed_universe_check.py \
         --config "<WS>/.context/btf/handoff-20261022/E-fixtures/etf_510210_parquet.yaml"
       → `510210.SH → fund_daily`，**MISMATCH**（能力表仅 daily），**exit=1**
       加 `--probe` → 追加 `bars=0  ZERO_BARS`（仍 exit=1）

    3) `--selftest`：8 条用例（ETF+parquet→MISMATCH / ETF+mixed→OK / 股票+parquet→OK /
       指数+mixed→OK / 指数+parquet→MISMATCH / 北交所+parquet→OK / 深ETF+mixed→OK /
       未注册 feed 必抛错），**exit=0**

    4) `--root`（本区 `.context` 全扫，895 配置 / 21,614 标的）：OK 21,611 / MISMATCH 3 /
       SKIP 491 配置（374 `source=all` + 116 非 btf 配置 + 1 非法 YAML）→ **exit=1**。
       3 条 MISMATCH 中 **1 条是历史 run 的真实命中**：
       `回测产物/runs/20261014_090404_b88b8e`（510210.SH + parquet，`n_fills=0` 的 EMPTY run）
       ——即 U-7「零成交 4 个」里那个 `n_days=0` 的真异常，**根因就是 P17**。

已知限制
--------
- `run.universe.source: all` **不枚举**（SKIP）：需 stock_basic 期间并集 + 逐日循环，
  本工具不做静态推断。结构提示（依据 `universe_resolver.py:129-168` `_all` →
  `feed.universe_span` → `stock_basic`）：`all` 只含**股票段**，路由恒为 `daily`，
  故 parquet 在 `source=all` 下不会因 ETF 而零 bar——但这是说明，不是判定。
- `source: index` 需镜像 `index_weight`；表缺失/无快照 → SKIP 并打印原因（不猜）。
- `--probe` 需 `--data-dir`（或 `BTF_DATA_DIR`）指向**当前镜像**；平台默认
  `MARKET_DATA_DIR` 可能是旧镜像（`D:\\全量数据\\market_data`，逐标的布局），
  对它会抛「by_year 布局含非年份文件」——工具按环境错误处理并提示改 `--data-dir`。
- 本工具只判「路由是否可能读到 bar」，**不判**复权/停牌/涨跌停/可执行性；
  也不替代 `data_watermark_check.py`（水位）与 `zero_fill_check.py`（成交）。
- 混扫 `.context` 这类多用途目录会有噪声（策略片段 / helm 模板 / pytest 产物）：
  本工具按「无 `run.strategy` 且无 `data.feed`」标 `非 btf 回测配置`（显式列出，不静默）
  并跳过 `pytest_tmp` / `_extracted` 目录；但这不是配置过滤器的替代品，
  **建议扫具体项目产物目录**（如 `.context/xxx/outputs`）以获得可执行的通过结论。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# ── btf 只读导入（本工具位于 <repo>/tools/，repo 根 = 上一级）──
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from btf.config.paths import MARKET_DATA_DIR

DEFAULT_DATA_DIR = os.environ.get("BTF_DATA_DIR") or str(MARKET_DATA_DIR)

_SKIP_DIRS = {".venv-btf", "__pycache__", ".git", ".ruff_cache", "node_modules",
              ".ipynb_checkpoints", "pytest_tmp", "_extracted"}


@dataclass(frozen=True)
class FeedCap:
    """单个 Feed 的日线表能力（tables = 该 Feed 实际能读到的日线表名）。"""

    tables: frozenset[str]
    evidence: tuple[str, ...]


FEED_CAPS: dict[str, FeedCap] = {
    "tushare_parquet": FeedCap(
        tables=frozenset({"daily"}),
        evidence=(
            "btf/data/feed.py:481-487  TushareParquetFeed.bars_of(..., daily_table: str = \"daily\")",
            "btf/data/feed.py:524-530  TushareParquetFeed.bars(... daily_table: str = \"daily\")",
            "btf/data/feed.py:309-311  docstring：表选择「由调用方声明」（engine/runtime 无调用点传该参数）",
        ),
    ),
    "mixed": FeedCap(
        tables=frozenset({"daily", "fund_daily", "index_daily"}),
        evidence=(
            "btf/data/feed.py:791-793  MixedDailyFeed.preload 按 daily_table_of 分组读表",
            "btf/data/feed.py:810-820  bars_of(daily_table=daily_table or daily_table_of(symbol))",
            "btf/data/feed.py:855-866  bars() 按 daily_table_of 逐表归并（三表同列）",
            "btf/engine/loop.py:292   engine 对 index_daily 单独处理复权语义（指数原样）",
        ),
    ),
}


class EnvError(RuntimeError):
    """环境/输入错误（→ exit 2）。"""


def _ymd(s: str) -> str:
    """'2021-01-04' / '20210104' → '20210104'（非 8 位抛 ValueError）。"""
    t = str(s).strip().replace("-", "").replace("/", "")
    if len(t) != 8 or not t.isdigit():
        raise ValueError(f"非法日期：{s!r}")
    return t


# ── 路由判定（纯逻辑，可离线 selftest）──
def route_table(symbol: str) -> str:
    """平台真源路由（禁止本工具另写一套判定——双真源必然漂移）。"""
    from btf.data.tables_meta import daily_table_of

    return daily_table_of(symbol)


def judge(feed: str, symbol: str) -> tuple[str, str, str]:
    """返回 (table, verdict, detail)；verdict ∈ {OK, MISMATCH}。"""
    cap = FEED_CAPS.get(feed)
    if cap is None:
        raise EnvError(
            f"未注册的 feed={feed!r}：平台注册表仅 "
            f"{sorted(FEED_CAPS)}（依据 btf/registry/catalog.py:65-66）")
    table = route_table(symbol)
    hit = table in cap.tables
    detail = (f"能力表 {'含' if hit else '不含'} {table}（{feed} 支持 "
              f"{'/'.join(sorted(cap.tables))}）")
    return table, ("OK" if hit else "MISMATCH"), detail


# ── 配置读取 / 宇宙枚举 ──
def load_config(path: Path) -> dict:
    """读 yaml/yml/json 配置；manifest.json 自动取 config_effective。"""
    suf = path.suffix.lower()
    try:
        if suf in (".yaml", ".yml"):
            import yaml

            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        elif suf == ".json":
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            raise EnvError(f"不支持的配置后缀：{path.name}（仅 .yaml/.yml/.json）")
    except EnvError:
        raise
    except Exception as exc:
        raise EnvError(f"读取失败 {path}：{type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise EnvError(f"配置根不是映射：{path}")
    if "config_effective" in data and isinstance(data["config_effective"], dict):
        data = data["config_effective"]
    return data


def enumerate_universe(
    universe: dict | None, period: dict | None, data_dir: Path,
) -> tuple[list[str], str | None, str]:
    """返回 (symbols, skip_reason, detail)。skip_reason 非空 ⇒ 该配置记 SKIP。"""
    if not universe:
        return [], "run.universe 缺失（无法确定宇宙；平台侧默认 explicit 空清单）", "universe=∅"
    source = str(universe.get("source", "explicit"))
    if source == "explicit":
        syms = [str(s) for s in (universe.get("symbols") or [])]
        if not syms:
            return [], "source=explicit 但 symbols 为空（零标宇宙）", "explicit"
        return syms, None, f"explicit（{len(syms)} 标的）"
    if source == "all":
        return [], (
            "source=all 不可枚举（需 stock_basic 期间并集 + 逐日循环；本工具不做静态推断）。"
            "结构提示：_all 经 feed.universe_span → stock_basic 股票池，路由恒 daily"
            "（universe_resolver.py:129-168），故 parquet 在 all 下无 ETF 零 bar 风险"), "all（SKIP）"
    if source == "index":
        name = universe.get("index")
        if not name:
            return [], "source=index 但缺 run.universe.index", "index（缺 index 字段）"
        if not period or not period.get("start") or not period.get("end"):
            return [], "source=index 但缺 run.period.start/end（无法复算成分快照）", "index"
        try:
            from btf.data.index_universe import IndexUniverseProvider, resolve_index

            code = resolve_index(str(name))
            prov = IndexUniverseProvider(data_dir)
            codes = prov.union_codes(code, _ymd(period["start"]), _ymd(period["end"]))
        except Exception as exc:
            return [], (f"index 成分不可枚举（{type(exc).__name__}: {exc}）——"
                        f"核对 --data-dir={data_dir} 的 index_weight"), "index（不可枚举）"
        if not codes:
            return [], (f"index={name}（{code}）在区间内无 index_weight 成分快照"
                        f"（--data-dir={data_dir}）"), "index（无快照）"
        return codes, None, f"index={name}（{code}，并集 {len(codes)} 标的）"
    return [], f"run.universe.source={source!r} 未支持（平台仅 explicit|all|index）", source


def _period_of(cfg: dict) -> dict | None:
    run = cfg.get("run") or {}
    p = run.get("period")
    return p if isinstance(p, dict) else None


# ── 单配置检查 ──
def check_config(
    path: Path, feed_override: str | None, data_dir: Path, probe: bool,
) -> dict:
    cfg = load_config(path)
    data = cfg.get("data") or {}
    feed = feed_override or data.get("feed")
    feed = str(feed) if feed else None
    if feed and feed not in FEED_CAPS:
        raise EnvError(
            f"{path.name}: data.feed={feed!r} 未注册（平台仅 {sorted(FEED_CAPS)}）")
    universe = (cfg.get("run") or {}).get("universe")
    period = _period_of(cfg)
    res: dict = {
        "config": str(path), "feed": feed, "feed_known": feed in FEED_CAPS,
        "period": f"{period.get('start')}..{period.get('end')}" if period else None,
        "rows": [], "skips": [], "probe": [],
    }
    if not feed:
        if not (cfg.get("run") or {}).get("strategy"):
            res["skips"].append(
                "非 btf 回测配置（无 run.strategy 且无 data.feed；如策略片段/脚手架/图表模板）——跳过")
        else:
            res["skips"].append(
                "data.feed 缺失（无法判定路由；schema 要求 data.feed 必填）")
        res["source_detail"] = "—"
        return res
    syms, skip, detail = enumerate_universe(universe, period, data_dir)
    res["source_detail"] = detail
    if skip:
        res["skips"].append(skip)
        return res
    for sym in dict.fromkeys(syms):
        table, verdict, detail_row = judge(feed, sym)
        res["rows"].append({"symbol": sym, "table": table,
                            "verdict": verdict, "detail": detail_row})
    if probe:
        res["probe"] = _probe(feed, list(dict.fromkeys(syms)), period, data_dir)
    return res


def _probe(feed: str, symbols: list[str], period: dict | None, data_dir: Path) -> list[dict]:
    """真起 Feed 数 bar（只读）。0 根 bar → ZERO_BARS（P17 表症，须人判）。"""
    from btf.data.feed import MixedDailyFeed, TushareParquetFeed
    from btf.domain.types import TradingDate

    if not period or not period.get("start") or not period.get("end"):
        return [{"estimate": True, "status": "PROBE_SKIP",
                 "note": "缺 run.period.start/end，无法数 bar（非静默跳过）"}]
    cls = {"tushare_parquet": TushareParquetFeed, "mixed": MixedDailyFeed}[feed]
    f = cls(data_dir)
    s = TradingDate.from_ymd(_ymd(period["start"]))
    e = TradingDate.from_ymd(_ymd(period["end"]))
    out: list[dict] = []
    for sym in symbols:
        try:
            n = len(f.bars_of(sym, s, e))
            out.append({"symbol": sym, "bars": n,
                        "status": "ZERO_BARS" if n == 0 else "HAS_BARS"})
        except Exception as exc:
            out.append({"symbol": sym, "bars": None, "status": "PROBE_ERROR",
                        "note": f"{type(exc).__name__}: {exc}"})
    return out


# ── selftest（负向测试：错配必须被判出）──
def selftest() -> bool:
    cases = [
        # (feed, symbol, 期望 verdict, 期望 table, 说明)
        ("tushare_parquet", "510210.SH", "MISMATCH", "fund_daily", "ETF+parquet → 能力表无 fund_daily（P17 本体）"),
        ("mixed", "510210.SH", "OK", "fund_daily", "ETF+mixed → 代码段路由 fund_daily"),
        ("tushare_parquet", "600519.SH", "OK", "daily", "股票+parquet → 默认 daily 命中"),
        ("mixed", "000300.SH", "OK", "index_daily", "指数+mixed → 路由 index_daily（feed.py:819；实测 81 bar）"),
        ("tushare_parquet", "000300.SH", "MISMATCH", "index_daily", "指数+parquet → 能力表无 index_daily"),
        ("tushare_parquet", "920627.BJ", "OK", "daily", "北交所(92 段)+parquet → daily（非 ETF 误判）"),
        ("mixed", "159915.SZ", "OK", "fund_daily", "深市 ETF+mixed → fund_daily"),
    ]
    ok = True
    print(f"{'feed':<16}{'symbol':<12}{'table':<13}{'verdict':<10}期望  结果")
    print("-" * 72)
    for feed, sym, want_v, want_t, why in cases:
        try:
            table, verdict, _ = judge(feed, sym)
        except Exception as exc:
            print(f"{feed:<16}{sym:<12}{'ERR':<13}{type(exc).__name__:<10}{want_v}  ✗  ({why})")
            ok = False
            continue
        good = (table == want_t and verdict == want_v)
        print(f"{feed:<16}{sym:<12}{table:<13}{verdict:<10}{want_v}  {'✓' if good else '✗'}  ({why})")
        ok &= good
    # 负向：未注册 feed 必须抛错（不得静默按 daily 判定）
    try:
        judge("memory_feed", "510210.SH")
        print(f"{'memory_feed':<16}{'510210.SH':<12}{'—':<13}{'NO-RAISE':<10}ValueError  ✗  (未注册 feed 必须抛错)")
        ok = False
    except EnvError as exc:
        print(f"{'memory_feed':<16}{'510210.SH':<12}{'—':<13}{'EnvError':<10}ValueError  ✓  (未注册 feed 抛错：{str(exc)[:38]}…)")
    except Exception as exc:
        print(f"{'memory_feed':<16}{'510210.SH':<12}{'—':<13}{type(exc).__name__:<10}ValueError  ✗  (异常类型不符)")
        ok = False
    print(f"[selftest] {'PASS' if ok else 'FAIL'}（{len(cases)+1} 条用例，含负向）")
    return ok


# ── 输出 ──
def _iter_configs(root: Path):
    for p in sorted(root.rglob("*")):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        n = p.name.lower()
        if n.endswith((".yaml", ".yml")):
            yield p
        elif n == "manifest.json":
            yield p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Feed × 标的路由预检（P17：tushare_parquet 跑 ETF/指数静默零 bar）")
    ap.add_argument("--config", help="单个配置（.yaml/.yml/.json）")
    ap.add_argument("--root", help="递归扫描根目录（*.yaml/*.yml + manifest.json）")
    ap.add_argument("--feed", help="覆盖 data.feed（试跑对照：改成 mixed 看是否变 OK）")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help=f"镜像根（仅 index 枚举与 --probe 用；默认 {DEFAULT_DATA_DIR}）")
    ap.add_argument("--probe", action="store_true", help="真起 Feed 数 bar（ZERO_BARS 也判不通过）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    try:
        if a.selftest and not selftest():
            print("[result] exit=1（selftest 失败）")
            return 1
        if a.selftest and not (a.config or a.root):
            print("[result] exit=0（仅 selftest 模式，未检查配置）")
            return 0

        if not a.config and not a.root:
            print("[env] 需要 --config 或 --root（或 --selftest）")
            print("[result] exit=2（环境错误）")
            return 2
        if a.feed and a.feed not in FEED_CAPS:
            print(f"[env] --feed={a.feed!r} 未注册（平台仅 {sorted(FEED_CAPS)}；"
                  f"依据 btf/registry/catalog.py:65-66）")
            print("[result] exit=2（环境错误）")
            return 2

        data_dir = Path(a.data_dir)
        paths: list[Path] = []
        if a.config:
            p = Path(a.config)
            if not p.is_file():
                print(f"[env] 配置不存在：{p}")
                print("[result] exit=2（环境错误）")
                return 2
            paths.append(p)
        if a.root:
            r = Path(a.root)
            if not r.is_dir():
                print(f"[env] --root 不是目录：{r}")
                print("[result] exit=2（环境错误）")
                return 2
            paths.extend(_iter_configs(r))
            if not paths:
                print(f"[env] {r} 下未找到配置（*.yaml/*.yml/manifest.json）")
                print("[result] exit=2（环境错误）")
                return 2

        results, config_errors = [], []
        for p in paths:
            try:
                results.append(check_config(p, a.feed, data_dir, a.probe))
            except EnvError as exc:
                # 批量扫描时单个坏配置不得中断全局：显式记入 ERROR 清单（不静默）
                config_errors.append((str(p), str(exc)))
                results.append({"config": str(p), "feed": None, "feed_known": False,
                                "period": None, "rows": [], "probe": [],
                                "source_detail": "读配置失败",
                                "skips": [f"配置无法解析（已列入 ERROR 清单）：{exc}"]})

        # 汇总
        n_ok = n_bad = n_skip_cfg = n_judged = 0
        mismatches, zero_bars, skips, probe_errors = [], [], [], []
        for r in results:
            rows = r["rows"]
            if rows:
                n_judged += len(rows)
                n_ok += sum(1 for x in rows if x["verdict"] == "OK")
                bad = [x for x in rows if x["verdict"] == "MISMATCH"]
                n_bad += len(bad)
                mismatches.extend((r["config"], x) for x in bad)
            if not rows:
                n_skip_cfg += 1
            for s in r["skips"]:
                skips.append((r["config"], s))
            for pr in r["probe"]:
                if pr.get("status") == "ZERO_BARS":
                    zero_bars.append((r["config"], pr))
                elif pr.get("status") in ("PROBE_ERROR", "PROBE_SKIP"):
                    probe_errors.append((r["config"], pr))

        # 退出码优先级：MISMATCH/零 bar → 1；解析错误或全 SKIP（空转）→ 2；否则 0
        bad = bool(n_bad) or bool(zero_bars) or bool(probe_errors)
        if bad:
            code, msg = 1, "存在 MISMATCH/零 bar，拒绝开跑"
        elif config_errors:
            code, msg = 2, f"{len(config_errors)} 个配置无法解析"
        elif n_judged == 0:
            code, msg = 2, "无可判定标的：全部 SKIP —— 空转不当通过"
        else:
            code, msg = 0, "路由全部 OK"

        if a.json:
            # JSON 模式：判定摘要并入同一文档（不再向 stdout 追加非 JSON 行，保证可解析）
            print(json.dumps({"results": results,
                              "summary": {"configs": len(results),
                                          "judged_symbols": n_judged, "ok": n_ok,
                                          "mismatch": n_bad, "skip_configs": n_skip_cfg,
                                          "zero_bars": len(zero_bars),
                                          "config_errors": len(config_errors),
                                          "exit_code": code, "message": msg}},
                             ensure_ascii=False, indent=2, default=str))
            return code
        if len(paths) > 1:
            # 批量模式：每配置一行判定（显式），非 OK 才展开明细
            print(f"{'判定':<10}{'feed':<17}{'宇宙':<26}{'symbols':<9}config")
            print("-" * 78)
            for r in results:
                rows = r["rows"]
                bad = [x for x in rows if x["verdict"] == "MISMATCH"]
                if bad:
                    tag = "MISMATCH"
                elif rows:
                    tag = "OK"
                else:
                    tag = "SKIP"
                print(f"{tag:<10}{r['feed']!s:<17}{r['source_detail'][:24]:<26}"
                      f"{len(rows):<9}{Path(r['config']).name}  ← {r['config']}")
                for x in bad:
                    print(f"           ✗ {x['symbol']} → {x['table']}（{x['detail']}）")
                for s in r["skips"]:
                    print(f"           ⏭ {s[:150]}")
                for pr in r["probe"]:
                    if pr.get("status") in ("ZERO_BARS", "PROBE_ERROR"):
                        print(f"           ⚠ probe {pr.get('symbol')} {pr['status']} bars={pr.get('bars')}")
        else:
            for r in results:
                feed_s = r["feed"] or "（缺 data.feed）"
                print("=" * 78)
                print(f"[config] {r['config']}")
                print(f"[config] feed={feed_s}  universe={r['source_detail']}  "
                      f"period={r['period']}")
                if r["rows"]:
                    print(f"  {'symbol':<12}{'路由表':<14}{'能力表命中':<12}verdict")
                    for x in r["rows"]:
                        hit = "含" if x["verdict"] == "OK" else "不含"
                        print(f"  {x['symbol']:<12}{x['table']:<14}{hit:<12}{x['verdict']}"
                              + ("" if x["verdict"] == "OK" else f"   ← {x['detail']}"))
                for pr in r["probe"]:
                    if "symbol" in pr:
                        print(f"  [probe] {pr['symbol']:<12}bars={pr['bars']}  {pr['status']}")
                    else:
                        print(f"  [probe] {pr['status']}: {pr.get('note')}")

            print()
            print("=" * 78)
            print(f"【判定】标的合计 {n_judged}：OK={n_ok} / MISMATCH={n_bad}"
                  f"；SKIP 配置 {n_skip_cfg} 个")
            print(f"【MISMATCH】{len(mismatches)} 条")
            for cfg, x in mismatches:
                print(f"  ✗ {Path(cfg).name}：{x['symbol']} 路由 {x['table']}——{x['detail']}")
            if config_errors:
                print(f"【ERROR（配置无法解析）】{len(config_errors)} 条")
                for cfg, err in config_errors:
                    print(f"  ✗ {cfg}：{err}")
            print(f"【SKIP】{len(skips)} 条"
                  + ("" if not skips else "（逐条列出，不静默）"))
            for cfg, s in skips:
                print(f"  ⏭ {Path(cfg).name}：{s}")
            if zero_bars:
                print(f"【ZERO_BARS】{len(zero_bars)} 条（--probe：路由 OK 但 0 根 bar，须人判）")
                for cfg, pr in zero_bars:
                    print(f"  ⚠ {Path(cfg).name}：{pr['symbol']} bars=0")
            if probe_errors:
                print(f"【PROBE 不可用】{len(probe_errors)} 条（显式列出）")
                for cfg, pr in probe_errors:
                    print(f"  ⚠ {Path(cfg).name}：{pr.get('symbol') or ''} {pr['status']} "
                          f"{pr.get('note','')}")

        # 退出码：见上（MISMATCH→1 / 解析错误或全 SKIP→2 / 全 OK→0）
        print(f"[result] exit={code}（{msg}）")
        return code
    except EnvError as exc:
        print(f"[env] {exc}")
        print("[result] exit=2（环境错误）")
        return 2
    except Exception as exc:
        print(f"[env] {type(exc).__name__}: {exc}")
        print("[result] exit=2（环境错误）")
        return 2


if __name__ == "__main__":
    sys.exit(main())
