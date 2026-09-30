# -*- coding: utf-8 -*-
"""config 四层合并 + Schema 校验单测（ADR-4 / 06 §11.5；M1 任务 4.3）。

验收样例 = 06 §11.5 示例原文（含 1_000_000 YAML 1.1 整数、flow 集合、
嵌套 rules 段）；H2：rules 段规则参数字面量拒绝 + override:true 强制。
"""
from __future__ import annotations

import pytest
from btf.config.loader import (
    ConfigSourceError,
    deep_merge,
    load_config,
)
from btf.config.mask import mask_config
from btf.config.validation import ConfigError, validate

#: 06 §11.5 示例（验收样例——键序/值保持文档原样；两处 v0.5 订正见下）
SAMPLE_06 = """\
schema_version: "backtest.v1"
run:
  strategy: "myapp.strategies:RotationStrategy"
  universe: {source: "index", index: "hs300", as_of: "2020-01-01", include_delisted: true}
  period: {start: "2018-01-01", end: "2024-12-31", anchor: "2024-12-31"}
  initial_cash: 1_000_000
data:
  feed: "tushare_parquet"
  dataset: {version: "tushare.v5.3", rules: "v7.7"}
execution:
  handler: "next_open"
  cost_model: "tiered_v1"
  slippage: {model: "none", params: {}}
risk:
  rules: [{name: "max_weight", params: {limit: 0.10}}, {name: "cash_check"}, {name: "tradability"}]
rules:
  source: "mirror_json"
  path: "D:/量化策略/赤潮/rules_mirror_v77.json"
  overrides: []
seed: {master: 20260926}
report:
  sections: [summary, equity, drawdown, monthly_heat, trades, assumptions]
  benchmark: "000300.SH"
"""


class TestSample06:
    """06 §11.5 示例即验收样例：原文必须通过 Schema 校验。"""

    def test_sample_validates(self):
        cfg = load_config(SAMPLE_06)
        validate(cfg)
        assert cfg["run"]["initial_cash"] == 1_000_000   # YAML 1.1 下划线整数
        assert cfg["rules"]["source"] == "mirror_json"
        assert "completeness" not in cfg["data"]   # 19 号 P1-5：死键已删除
        assert "benchmark" in cfg["report"]        # 基准对比键（R2.5 消费）

    def test_defaults_fill_execution(self):
        cfg = load_config(SAMPLE_06)
        assert cfg["execution"]["rebalancer"] == "full"   # 默认层补齐
        assert cfg["risk"]["rules"][1] == {"name": "cash_check"}

    def test_defaults_standalone_minimal(self):
        """最小配置（仅必填段）经默认层补齐后可校验通过。"""
        minimal = """
schema_version: "backtest.v1"
run:
  strategy: "tests.fixtures.buyhold:ConfigBuyHold"
  period: {start: "2015-01-05", end: "2015-01-09"}
data:
  feed: "memory"
"""
        cfg = load_config(minimal)
        validate(cfg)
        assert cfg["execution"]["handler"] == "next_open"
        assert cfg["execution"]["cost_model"] == "tiered_v1"   # M2 5.1 默认分段费率
        assert "completeness" not in cfg["data"]        # 19 号 P1-5：死键已删
        # X-7（Q1=A）：默认层**不声明**逃生开关 → 空链装配期即 ConfigError
        assert "allow_empty_chain" not in cfg["risk"]


class TestLayeredMerge:
    def test_env_overrides_default(self):
        cfg = load_config(None, environ={"BTF_EXECUTION__COST_MODEL": "flat_rate"})
        assert cfg["execution"]["cost_model"] == "flat_rate"

    def test_yaml_overrides_env(self):
        cfg = load_config(SAMPLE_06,
                          environ={"BTF_EXECUTION__COST_MODEL": "flat_rate"})
        assert cfg["execution"]["cost_model"] == "tiered_v1"

    def test_cli_overrides_yaml(self):
        cfg = load_config(SAMPLE_06,
                          overrides={"execution": {"cost_model": "zero"}})
        assert cfg["execution"]["cost_model"] == "zero"
        assert cfg["execution"]["handler"] == "next_open"   # 其余键保留

    def test_env_value_coercion(self):
        """类型推断：字符串 → int / bool（X-6：原用 `BTF_DATA__COMPLETENESS__`
        为已删除的死键背书，改用真实存在的 `run.params` 路径）。"""
        cfg = load_config(None, environ={
            "BTF_RUN__INITIAL_CASH": "2000000",
            "BTF_RUN__PARAMS__TOP_K": "3",
            "BTF_RUN__UNIVERSE__INCLUDE_DELISTED": "false",
        })
        assert cfg["run"]["initial_cash"] == 2_000_000
        assert cfg["run"]["params"]["top_k"] == 3
        assert cfg["run"]["universe"]["include_delisted"] is False

    def test_path_env_skipped(self):
        cfg = load_config(None, environ={"BTF_DATA_DIR": "X:/elsewhere"})
        assert "data_dir" not in cfg

    def test_deep_merge_dict_merge_and_replace(self):
        base = {"a": {"x": 1, "y": 2}, "b": [1]}
        deep_merge(base, {"a": {"y": 9, "z": 3}, "b": [2]})
        assert base == {"a": {"x": 1, "y": 9, "z": 3}, "b": [2]}


class TestH2RulesLiteralRejection:
    def _rules_cfg(self, rules_section: dict) -> dict:
        cfg = load_config(SAMPLE_06)
        cfg["rules"] = rules_section
        return cfg

    def test_rule_literal_rejected(self):
        cfg = self._rules_cfg({
            "source": "mirror_json",
            "params": {"pe_ttm_max": 30},     # 规则参数字面量（H2 红线）
        })
        with pytest.raises(ConfigError) as e:
            validate(cfg)
        msg = str(e.value)
        assert "rules" in msg and "'params' was unexpected" in msg

    def test_override_requires_true(self):
        cfg = self._rules_cfg({
            "source": "mirror_json",
            "overrides": [{"rule": "l2_filter", "param": "pe_ttm_max",
                           "value": 30, "override": False}],
        })
        with pytest.raises(ConfigError):
            validate(cfg)

    def test_explicit_override_passes(self):
        cfg = self._rules_cfg({
            "source": "mirror_json",
            "overrides": [{"rule": "l2_filter", "param": "pe_ttm_max",
                           "value": 30, "override": True}],
        })
        validate(cfg)


class TestValidationMessages:
    def test_missing_required_lists_all(self):
        cfg = {"schema_version": "backtest.v1"}
        with pytest.raises(ConfigError) as e:
            validate(cfg)
        assert len(e.value.problems) >= 2
        assert any("run" in p for p in e.value.problems)
        assert any("data" in p for p in e.value.problems)

    def test_error_contains_json_path_and_value(self):
        cfg = load_config(SAMPLE_06)
        cfg["run"]["period"]["start"] = "2018/01/01"
        with pytest.raises(ConfigError) as e:
            validate(cfg)
        assert any("run.period.start" in p for p in e.value.problems)

    def test_unknown_schema_version(self):
        with pytest.raises(ConfigError, match="未知契约版本"):
            validate({"schema_version": "backtest.v99"})

    def test_missing_schema_version(self):
        with pytest.raises(ConfigError, match="schema_version"):
            validate({})

    def test_bad_strategy_pattern(self):
        cfg = load_config(SAMPLE_06)
        cfg["run"]["strategy"] = "not-a-module-path"
        with pytest.raises(ConfigError, match=r"run\.strategy"):
            validate(cfg)


class TestMask:
    def test_sensitive_keys_masked(self):
        cfg = {
            "data": {"feed": "tushare_parquet",
                     "feed_params": {"tushare_token": "SECRET",
                                     "root": "D:/data"}},
            "run": {"api_key": "abc", "initial_cash": 100},
            "report": {"password": "x", "sections": ["summary"]},
        }
        m = mask_config(cfg)
        assert m["data"]["feed_params"]["tushare_token"] == "***"
        assert m["data"]["feed_params"]["root"] == "D:/data"
        assert m["run"]["api_key"] == "***"
        assert m["report"]["password"] == "***"
        assert m["run"]["initial_cash"] == 100
        assert cfg["data"]["feed_params"]["tushare_token"] == "SECRET"  # 原件不动

    def test_sensitive_mapping_whole_masked(self):
        m = mask_config({"credentials": {"user": "u", "pass": "p"}})
        assert m["credentials"] == "***"


class TestSourceErrors:
    def test_yaml_syntax_error(self):
        with pytest.raises(ConfigSourceError, match="YAML 解析失败"):
            load_config("run: [unclosed")

    def test_top_level_not_mapping(self):
        with pytest.raises(ConfigSourceError, match="顶层必须为映射"):
            load_config("- a\n- b\n")

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigSourceError, match="不可读"):
            load_config(tmp_path / "nope.yaml")
