# -*- coding: utf-8 -*-
"""D5 数据语义 / 审计主键守卫（REQ-20261121-06 · G2）

覆盖：
  ① `classify_ohlc_anomalies` 把「无成交 / 零点位」归为合法语义，真异常仍报出；
  ② `DB_AUDIT_PKEYS` 对 D5-E 实测的合法多行表不再使用过窄主键。
"""

import pandas as pd

from config import DB_AUDIT_PKEYS
from tools.db.audit import classify_ohlc_anomalies


def test_opt_no_trade_rows_are_legal():
    df = pd.DataFrame(
        {
            "ts_code": ["SR707P7000.ZCE", "BAD.ZCE"],
            "trade_date": ["20170419", "20170419"],
            "open": [0.0, 10.0],
            "high": [0.0, 9.0],
            "low": [0.0, 11.0],
            "close": [0.0, 9.5],
            "vol": [0.0, 5.0],
            "amount": [0.0, 100.0],
        }
    )
    r = classify_ohlc_anomalies(df, "opt_daily")
    assert r["legal_semantic"] == 1  # 无成交：OHL=0
    assert r["true_anomalies"] == 1  # high<low 的真异常


def test_index_zero_point_is_legal():
    df = pd.DataFrame(
        {
            "ts_code": ["395011.SZ", "BAD.CSI", "ZERO_BAD.CSI"],
            "trade_date": ["20261118", "20261118", "20261118"],
            "open": [None, 10.0, 5.0],
            "high": [None, 9.0, 6.0],
            "low": [None, 11.0, 1.0],
            "close": [0.0, 9.5, 0.0],
            "vol": [None, 5.0, 5.0],
            "amount": [None, 100.0, 100.0],
        }
    )
    r = classify_ohlc_anomalies(df, "index_daily")
    assert r["legal_semantic"] == 1
    # 第二行 high<low；第三行 close=0 但 OHL 有值（不得被零点位掩码吞掉）
    assert r["true_anomalies"] == 2


def test_daily_ohlc_anomaly_stays_true():
    df = pd.DataFrame(
        {
            "ts_code": ["000003.SZ"],
            "trade_date": ["19920131"],
            "open": [20.15],
            "high": [20.85],
            "low": [20.81],
            "close": [20.75],
            "vol": [616.0],
            "amount": [6345.0],
        }
    )
    r = classify_ohlc_anomalies(df, "daily")
    assert r["true_anomalies"] == 1 and r["legal_semantic"] == 0


def test_d5_pkeys_not_too_narrow():
    assert DB_AUDIT_PKEYS["stk_holdertrade"] is None
    assert DB_AUDIT_PKEYS["fina_mainbz"] is None
    assert DB_AUDIT_PKEYS["pledge_detail"] is None
    assert DB_AUDIT_PKEYS["repurchase"] is None
    assert DB_AUDIT_PKEYS["fina_audit"] is None
    assert "ann_date" in DB_AUDIT_PKEYS["top10_holders"]
    assert "ann_date" in DB_AUDIT_PKEYS["top10_floatholders"]
    assert "freq" in DB_AUDIT_PKEYS["fut_weekly_monthly"]
    assert {"level", "src"} <= set(DB_AUDIT_PKEYS["index_classify"])
