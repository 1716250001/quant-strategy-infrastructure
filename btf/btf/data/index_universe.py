# -*- coding: utf-8 -*-
"""指数成分宇宙：index_weight 月末快照机器复算（v0.5 V5-4；05 §9.4）。

PIT 口径（05 §9.4「月末快照机器复算」+ 09 §14.7 B2）：
    成分在查询日 d 可用 ⇔ 取**快照日 < d** 的最新一份（d 之前已发布的最后
    月末快照）。月末快照（如 20160129）在次月首个交易日（20160201）生效
    ——同月末当日不可用（快照当日收盘后才完整，不前视）。

别名表：常见指数短名 → index_code（run.universe.index 既可写别名也可写
index_code；未登记别名**显式报错**，不做模糊匹配）。

引擎接线（runtime `universe.source=index`）：instruments = 回测期间各月
成分的**并集**（静态超集——引擎宇宙为静态身份，逐月切换由策略消费
`rt.index_universe` 月度映射）。
"""
from __future__ import annotations

from pathlib import Path

import pyarrow.compute as pc

from btf.config.paths import MARKET_DATA_DIR

#: 常见指数别名（登记制——未列出的指数直接写 index_code）
INDEX_ALIASES: dict[str, str] = {
    "hs300": "000300.SH",     # 沪深300
    "zz500": "000905.SH",     # 中证500
    "sz50": "000016.SH",      # 上证50
    "zz1000": "000852.SH",    # 中证1000
    "cyb": "399006.SZ",       # 创业板指
}


def resolve_index(name: str) -> str:
    """指数名 → index_code（别名优先；原样返回已是 index_code 者）。"""
    return INDEX_ALIASES.get(name, name)


class IndexUniverseProvider:
    """index_weight → 月度成分（PIT：快照日 < 查询日）。"""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else MARKET_DATA_DIR
        # EX-4 完全体：取数**只经 core**（原直呼 `pq.read_table`）
        from btf.data.core import YearTableStore

        self._store = YearTableStore(self.root)
        self._snapshots: dict[str, list[str]] = {}      # index_code → 升序快照日
        self._rows: dict[str, dict[str, list[str]]] = {}  # code → {日: [con]}

    # ── 载入（每指数一次）──
    def _load(self, index_code: str) -> None:
        if index_code in self._rows:
            return
        table = self._store.read_file(
            "index_weight", columns=["index_code", "con_code", "trade_date"])
        if table is None:
            raise FileNotFoundError(
                f"index_weight 表缺失: {self.root / 'index_weight'}（指数成分宇宙 "
                f"V5-4 的数据前提）")
        table = table.filter(pc.equal(table.column("index_code"), index_code))
        rows: dict[str, list[str]] = {}
        for con, ymd in zip(table.column("con_code").to_pylist(),
                            table.column("trade_date").to_pylist(), strict=True):
            rows.setdefault(ymd, []).append(con)
        for day in rows:
            rows[day].sort()              # 确定性：成分列表升序
        self._rows[index_code] = rows
        self._snapshots[index_code] = sorted(rows)

    # ── 查询 ──
    def snapshots(self, index_code: str) -> list[str]:
        """全部快照日（升序）。"""
        self._load(index_code)
        return list(self._snapshots[index_code])

    def codes_before(self, index_code: str, ymd: str) -> list[str]:
        """查询日 ymd 可用的成分（最新**快照日 < ymd**；无 → 空=诚实无数据）。"""
        self._load(index_code)
        chosen: str | None = None
        for day in self._snapshots[index_code]:
            if day < ymd:
                chosen = day
            else:
                break
        return list(self._rows[index_code][chosen]) if chosen else []

    def monthly_codes(self, index_code: str, start_ymd: str,
                      end_ymd: str) -> dict[str, list[str]]:
        """逐月宇宙：{YYYYMM: 成分列表}——月宇宙 = 该月首日**之前**的最新快照。

        PIT：月首日前已发布的最后月末快照（即上月末或更早）；无快照的月
        → 空列表（不前向填充——诚实无数据）。
        """
        out: dict[str, list[str]] = {}
        year, month = int(start_ymd[:4]), int(start_ymd[4:6])
        end_key = end_ymd[:6]
        while f"{year:04d}{month:02d}" <= end_key:
            key = f"{year:04d}{month:02d}"
            out[key] = self.codes_before(index_code, f"{key}01")
            month += 1
            if month > 12:
                year, month = year + 1, 1
        return out

    def union_codes(self, index_code: str, start_ymd: str,
                    end_ymd: str) -> list[str]:
        """期间各月成分并集（引擎静态宇宙超集，升序）。"""
        monthly = self.monthly_codes(index_code, start_ymd, end_ymd)
        return sorted({code for codes in monthly.values() for code in codes})


__all__ = ["INDEX_ALIASES", "IndexUniverseProvider", "resolve_index"]
