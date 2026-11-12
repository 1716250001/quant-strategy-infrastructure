# -*- coding: utf-8 -*-
"""D-6（19 号 §275）：`bt data quality` 三态退出码 ＋ `--json` stdout 纯净。

铁律新 15（变异性）：RC=1 用例**故意注入** 3 行 OHLC 自相矛盾——若 CC-1 漏检
或用 `n_findings > 0` 之外的判据写松，用例即变红。
退出码契约（D6-3）：0 = `report.ok`（CC-7 只报不阻断）／1 = 存在阻断型 finding／
2 = 用法或前置错误（日期格式、表名打错、数据根不可用）。
"""
from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from btf.cli.main import main

pytestmark = [pytest.mark.l4]

DAYS = ["20240102", "20240103", "20240104"]
SYM = "600000.SH"


def _write(root, table: str, rows: list[dict]) -> None:
    d = root / table
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), d / "2024.parquet")


def _daily(*, bad: bool = False) -> list[dict]:
    rows = []
    for d in DAYS:
        row = {"ts_code": SYM, "trade_date": d, "open": 10.0, "high": 10.0,
               "low": 10.0, "close": 10.0, "pre_close": 10.0, "vol": 1000.0,
               "amount": 10000.0}
        if bad:
            row["high"] = 9.0        # high < close ⇒ CC-1 自相矛盾
        rows.append(row)
    return rows


@pytest.fixture()
def root(tmp_path):
    """合成主库（干净基线：CC-1..CC-5 全 PASS；无 dividend ⇒ CC-7 合法 0）。"""
    r = tmp_path / "market"
    (r / "metadata").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"exchange": "SSE", "cal_date": d, "is_open": 1,
         "pretrade_date": DAYS[max(0, i - 1)]}
        for i, d in enumerate(DAYS)]), r / "metadata" / "trade_cal.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": SYM, "name": "浦发银行", "list_date": "19991110",
         "delist_date": None}]), r / "metadata" / "stock_basic.parquet")
    _write(r, "daily", _daily())
    _write(r, "stk_limit", [
        {"ts_code": SYM, "trade_date": d, "up_limit": 11.0, "down_limit": 9.0}
        for d in DAYS])
    _write(r, "adj_factor", [
        {"ts_code": SYM, "trade_date": d, "adj_factor": 1.0} for d in DAYS])
    return r


def _args(root, *extra: str) -> list[str]:
    return ["data", "quality", "--start", DAYS[0], "--end", DAYS[-1],
            "--root", str(root), *extra]


def test_clean_subset_rc0(root, capsys):
    """干净子集：RC=0，末行 `[ok] quality` ＋ 逐规则行 ＋ `用时`。"""
    rc = main(_args(root))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "[ok] quality" in out
    assert "用时" in out
    assert "OHLC 内部一致性" in out


def test_out_of_bounds_rows_rc1(root, capsys):
    """注入 3 行越界（high < close）⇒ RC=1 且末行为 `[fail]`。"""
    _write(root, "daily", _daily(bad=True))
    rc = main(_args(root))
    out = capsys.readouterr().out
    assert rc == 1, out
    assert out.strip().splitlines()[-1].startswith("[fail]"), out
    assert "阻断型 finding 3" in out


def test_bad_date_format_rc2(root, capsys):
    """`--start 2026-11-09` 非 YYYYMMDD ⇒ RC=2 ＋ `[error]`（不跑校验）。"""
    rc = main(["data", "quality", "--start", "2026-11-09",
               "--end", "20261109", "--root", str(root)])
    cap = capsys.readouterr()
    assert rc == 2
    assert "[error]" in cap.err and "YYYYMMDD" in cap.err
    assert "quality 开始" not in cap.out


def test_unknown_table_rc2(root, capsys):
    """表名打错 ⇒ RC=2（**不得**静默出空报告/假绿）。"""
    rc = main(_args(root, "--price-table", "no_such_tbl"))
    cap = capsys.readouterr()
    assert rc == 2
    assert "[error]" in cap.err and "no_such_tbl" in cap.err
    assert "[ok]" not in cap.out


def test_registered_table_without_shards_rc2(root, capsys):
    """已登记表但在该 root 下零分片 ⇒ RC=2（**不得**静默出空报告/假绿）。

    与 `test_unknown_table_rc2` 的区别（**两条不同代码路径，勿合并/勿删**）：
    上例是**未登记**表（`table_files` 抛 `ReaderError` → 归一为 `ValueError`）；
    本例是**已登记但盘上无分片**（`services.run_data_quality` 的 `if not files`
    闸）——后者拆掉即「表名合法、盘上没有」式的完美假绿。
    """
    rc = main(_args(root, "--limit-table", "dividend"))
    cap = capsys.readouterr()
    assert rc == 2
    assert "无分片" in cap.err
    assert "[ok]" not in cap.out


def test_json_stdout_is_single_object(root, capsys):
    """`--json`：stdout 只有一个可解析 JSON 对象，人话走 stderr（D6-4）。"""
    rc = main(_args(root, "--json"))
    cap = capsys.readouterr()
    assert rc == 0
    payload = json.loads(cap.out)          # 横幅混入即解析失败
    assert payload["contract_version"] == "dataquality.v1"
    assert payload["ok"] is True
    assert "[ok] quality" in cap.err
