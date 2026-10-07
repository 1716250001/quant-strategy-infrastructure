# -*- coding: utf-8 -*-
"""L1 单测：`ConfigError` pickle 往返（**N11**，19 号 §73.4 / §75.1）。

根因：`__init__(problems)` 未实现 `__reduce__` ⇒ pickle 以
`args=(已格式化消息字符串,)` 重放构造 ⇒ **字符串被当 list**：`len()` 取**字符数**
（假计数）＋ `enumerate()` **逐字符**（`bt optimize` 进程池实测「1 处 ⇒ 62 处」）。

判据（§75.1）：
  A（决定性）往返后 `str()` 与原始**逐字相等**、假计数归零；
  B（端到端）**8 进程池**内抛 `ConfigError` ⇒ 主进程消息完整可读、计数＝实际问题数；
  C（零回归）构造签名 / `problems` 属性 / `__str__` 格式不变；内联路径行为不变。
"""
from __future__ import annotations

import pickle

import pytest
from btf.config.validation import ConfigError, validate

pytestmark = [pytest.mark.l1]


class TestConfigErrorPickleN11:
    def test_roundtrip_str_identical_single(self):
        """判据 A（1 处）：往返后逐字相等，且仍是「1 处」。"""
        err = ConfigError(["run.strategy: 加载失败 XXX（No module named YYY）"])
        back = pickle.loads(pickle.dumps(err))
        assert str(back) == str(err)
        assert back.problems == err.problems
        assert "（1 处）" in str(back)

    def test_roundtrip_no_fake_count_and_no_char_explosion(self):
        """判据 A（多问题）：行数 = 1 + 问题数（逐字符爆炸必须消失）。"""
        problems = [f"a.b{i}: 违例（值 {i!r}）" for i in range(7)]
        err = ConfigError(problems)
        back = pickle.loads(pickle.dumps(err))
        assert str(back) == str(err)
        assert f"（{len(problems)} 处）" in str(back)
        assert len(str(back).splitlines()) == 1 + len(problems)

    def test_validate_format_unchanged(self):
        """判据 C：既有产出格式（head ＋ 缩进编号）原样；往返不改。"""
        with pytest.raises(ConfigError) as ei:
            validate({"schema_version": "nope.v9"})
        msg = str(ei.value)
        assert msg.startswith("配置校验失败（1 处）：\n  [1] schema_version:")
        assert str(pickle.loads(pickle.dumps(ei.value))) == msg


class TestWorkerPoolConfigErrorN11:
    def test_pool_surfaces_readable_message(self):
        """判据 B：**8 进程池**子进程内 `ConfigError` ⇒ 主进程消息可读、
        header 计数 ＝ 实际列出条数（`run.initial_cash=0` 违 exclusiveMinimum）。"""
        from btf.optimize.grid import GridSearchOptimizer

        cfg = {
            "schema_version": "backtest.v1",
            "run": {
                "strategy": "tests.fixtures.buyhold:ConfigBuyHold",
                "params": {"symbol": "000001.SZ", "weight": 0.9},
                "universe": {"source": "explicit", "symbols": ["000001.SZ"]},
                "period": {"start": "2015-01-05", "end": "2015-01-09"},
                "initial_cash": 1_000_000,
            },
            "data": {"feed": "rc11a_normal_feed"},   # 校验先失败 ⇒ feed 不会真取
            "risk": {"rules": [], "allow_empty_chain": True},
        }
        with pytest.raises(ConfigError) as ei:
            GridSearchOptimizer(workers=8).run(
                cfg, {"run.initial_cash": [0]}, objective="final_nav")
        msg = str(ei.value)
        n_listed = len(msg.splitlines()) - 1
        assert n_listed >= 1
        assert f"（{n_listed} 处）" in msg, f"header 计数与列出条数不符：{msg!r}"
        assert "initial_cash" in msg
        # 逐字符爆炸的指纹：单字符问题行（如「  [1] 配」）不得出现
        assert not any(len(ln.strip()) <= len("  [9] 配") + 1
                       for ln in msg.splitlines()[1:])
