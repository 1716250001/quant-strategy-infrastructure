# -*- coding: utf-8 -*-
"""L4 集成：指数成分宇宙（v0.5 V5-4；主库 index_weight 真数据）。

验收（18 号 V5-4）：`hs300` 宇宙逐月与 index_weight 复算一致。
PIT 锚点：月末快照在**次月首日前**可用（快照日 < 查询日）；无前向填充。
"""
from __future__ import annotations

from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from btf.config.paths import MARKET_DATA_DIR
from btf.data.index_universe import (
    INDEX_ALIASES,
    IndexUniverseProvider,
    resolve_index,
)

pytestmark = [pytest.mark.l4]

ROOT = Path(MARKET_DATA_DIR)
IW = ROOT / "index_weight" / "index_weight.parquet"

requires_mainlib = pytest.mark.skipif(
    not IW.is_file(), reason=f"index_weight 不可用: {IW}")


def _raw_snapshot(index_code: str, ymd: str) -> list[str]:
    """直读主库单快照成分（复算基准——与 provider 无共享代码）。"""
    t = pq.read_table(IW, columns=["index_code", "con_code", "trade_date"])
    t = t.filter(pc.equal(t.column("index_code"), index_code))
    t = t.filter(pc.equal(t.column("trade_date"), ymd))
    return sorted(t.column("con_code").to_pylist())


class TestAliases:
    def test_known_aliases(self):
        assert resolve_index("hs300") == "000300.SH"
        assert resolve_index("zz500") == "000905.SH"
        assert resolve_index("sz50") == "000016.SH"

    def test_raw_index_code_passthrough(self):
        assert resolve_index("000300.SH") == "000300.SH"
        assert len(INDEX_ALIASES) >= 3


@requires_mainlib
class TestProviderPit:
    """PIT 口径：快照日 < 查询日（月末快照次月首日生效，不前视）。"""

    def test_hs300_monthly_matches_recompute(self):
        """验收主断言：hs300 逐月宇宙 == index_weight 复算（直读对比）。"""
        provider = IndexUniverseProvider(ROOT)
        monthly = provider.monthly_codes("000300.SH", "20190101", "20191231")
        assert len(monthly) == 12
        # 201902 宇宙 = 20190131 快照（2019 首个可用月——201901 无前月快照
        # 之外的数据？index_weight 起点更早，201901 → 20181228 快照）
        for month, codes in monthly.items():
            assert len(codes) == 300, month       # hs300 每快照恰 300 成分
            # 复算：该月首日前最后一份快照
            snapshots = provider.snapshots("000300.SH")
            prior = [d for d in snapshots if d < f"{month}01"][-1]
            assert codes == _raw_snapshot("000300.SH", prior), month

    def test_snapshot_day_itself_not_used(self):
        """查询日 = 快照日 → 用**前一份**（当日快照收盘后才完整，不前视）。"""
        provider = IndexUniverseProvider(ROOT)
        snaps = provider.snapshots("000300.SH")
        day = snaps[-1]
        prior = [d for d in snaps if d < day][-1]
        assert provider.codes_before("000300.SH", day) == \
            _raw_snapshot("000300.SH", prior)

    def test_no_forward_fill_before_first_snapshot(self):
        """首份快照之前 → 空列表（诚实无数据，不前向填充）。"""
        provider = IndexUniverseProvider(ROOT)
        first = provider.snapshots("000300.SH")[0]
        assert provider.codes_before("000300.SH", first) == []
        assert provider.codes_before("000300.SH", "19900101") == []

    def test_union_over_period(self):
        provider = IndexUniverseProvider(ROOT)
        union = provider.union_codes("000300.SH", "20190101", "20191231")
        monthly = provider.monthly_codes("000300.SH", "20190101", "20191231")
        assert union == sorted({c for codes in monthly.values() for c in codes})
        assert len(union) >= 300                # 有成分调整 → 并集 > 300

    def test_missing_table_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="index_weight"):
            IndexUniverseProvider(tmp_path).snapshots("000300.SH")


@requires_mainlib
class TestRuntimeIndexUniverse:
    """runtime 接线：universe.source=index（并集超集 + 月度映射注入）。"""

    def _config(self, **universe):
        return {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "btf.strategy.monthly:MonthlyEqualWeight",
                "params": {},
                "universe": {"source": "index", **universe},
                "period": {"start": "2019-01-01", "end": "2019-06-30"},
                "initial_cash": 1_000_000.0,
            },
            "data": {"feed": "tushare_parquet",
                     "feed_params": {"root": str(ROOT)}},
            # X-7（Q1=A）：显式声明无风控裸奔（默认层已拒绝空链）
            "risk": {"rules": [], "allow_empty_chain": True},
            "execution": {"handler": "next_open", "cost_model": "zero",
                          "rebalancer": "full"},
        }

    def test_index_source_builds_union_superset(self):
        from btf.runtime import BTFRuntime

        rt = BTFRuntime().load_config(self._config(index="hs300"))
        rt.build()
        provider = IndexUniverseProvider(ROOT)
        expected = provider.union_codes("000300.SH", "20190101", "20190630")
        assert sorted(rt.instruments) == expected
        assert rt.index_universe is not None
        assert len(rt.index_universe) == 6
        assert all(len(v) == 300 for v in rt.index_universe.values())
        # 策略自动注入月度映射（构造签名含 index_universe；MonthlyEqualWeight
        # 无 symbols 时若注入失败会在 build 期 ValueError）
        assert rt.strategy._index_universe is rt.index_universe

    def test_missing_index_param_config_error(self):
        from btf.config.validation import ConfigError
        from btf.runtime import BTFRuntime

        rt = BTFRuntime().load_config(self._config())
        with pytest.raises(ConfigError, match=r"run\.universe\.index"):
            rt.build()

    def test_unknown_source_config_error(self):
        from btf.config.validation import ConfigError
        from btf.runtime import BTFRuntime

        cfg = self._config(index="hs300")
        cfg["run"]["universe"] = {"source": "hs300"}     # 旧写法（v0.2 报错口径）
        rt = BTFRuntime().load_config(cfg)
        with pytest.raises(ConfigError, match=r"universe\.source"):
            rt.build()
