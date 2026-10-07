# -*- coding: utf-8 -*-
"""L3：**G7 真值锚**——603269.SH 2020-07-28 配股事件日分解（19 号 §81.2 G7）。

审查方锚点（§80.5）：`ΔNAV ＝ M×(P − Pr)`，**P ＝ 除权日有效价＝真实收盘
12.76**（有 bar 用真实收盘；**非**理论除权价 pex）；
`1,325×(12.76 − 7.55) ＝ 6,903.25`——与 18 号 §九·Y 实测（run 181930/182705
除权日 ON−OFF 差）逐位一致。
"""
from __future__ import annotations

import pathlib

import pytest

pytestmark = [pytest.mark.l3]


def test_603269_event_day_decomposition_real_close():
    from btf.config.paths import MARKET_DATA_DIR
    from btf.data.core import YearTableStore
    from btf.data.corp_action_probe import load_actions

    data = load_actions()
    assert data is not None, "仓库内缺 corp_actions.json"
    ev = next(x for x in data["rights"]
              if x["symbol"] == "603269.SH" and x["ex_date"] == "20200728")
    assert ev["ratio"] == pytest.approx(0.25)
    assert ev["price"] == pytest.approx(7.55)

    store = YearTableStore(pathlib.Path(MARKET_DATA_DIR))
    t = store.read_file("daily", year=2020,
                        columns=["ts_code", "trade_date", "close"])
    rows = [r for r in t.to_pylist()
            if r["ts_code"] == "603269.SH"
            and str(r["trade_date"]) == "20200728"]
    assert rows and rows[0]["close"] is not None, "除权日无真实 bar？"
    close = float(rows[0]["close"])
    assert close == pytest.approx(12.76, abs=0.005)      # 真实收盘

    m = int(5300 * 0.25)                                 # §78.3：登记日持仓 5,300
    assert m == 1325
    assert m * (close - 7.55) == pytest.approx(6_903.25, rel=1e-12)

    # 反证：pex（由除权前真实收盘推）≠ 真实收盘 ⇒ 用 pex 会得不同分解
    pex = (ev["x_actual"] + 0.25 * 7.55) / 1.25
    assert pex != pytest.approx(close, abs=0.05)
    assert m * (pex - 7.55) != pytest.approx(6_903.25, rel=1e-3)
