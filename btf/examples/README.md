# -*- coding: utf-8 -*-
"""示例策略目录：M2 交付 rotation.py 单文件示例（策略类+配置+注释即教程，
DXP 瘦身后取代 Cookiecutter 模板——终审轻微 3）。

**外部策略接入（19 号 §24.6 补文档，2026-09-28）**

策略可以放在**项目之外**（btf 核心零改动即可接入——19 号第五轮实测以
`代码\\_scratch\\ext_strategy\\ma_cross.py` 一次通过）。三步：

1. **写策略**（`StrategyBase` 子类，必须显式声明 `contract_version`）：

   ```python
   from btf.domain.contracts import CONTRACT_VERSION
   from btf.strategy.base import StrategyBase

   class MaCross(StrategyBase):
       contract_version = CONTRACT_VERSION      # 协商闸门：未声明 → 装配期拒载
       def __init__(self, fast: int = 5, slow: int = 20, **_params):
           super().__init__(); self.fast, self.slow = fast, slow
       def on_close(self, ctx, date) -> None:
           ...                                  # ctx.data / ctx.submit_order ...
   ```

2. **放进 import 路径**：`run.strategy` 只接受**点分模块名**（schema 正则禁止
   路径符），故策略包须在 `sys.path` 上——两种方式：
   - 放到 btf 根目录下的包（如 `ext_strategy/`）；或
   - 经环境变量注入：`set PYTHONPATH=D:\\path\\to\\your_strategies`（PowerShell
     会话级；实测 `PYTHONPATH=…\\_scratch` 后 `ext_strategy.ma_cross:MaCross` 可解析）。

3. **配置引用**：

   ```yaml
   run:
     strategy: "ext_strategy.ma_cross:MaCross"   # module:Class
     params: {fast: 5, slow: 20}                 # 构造参数直注入（名字须匹配，
                                                 # 类型须可 float/int 化）
   ```

运行：

```
bt config-check <your.yaml>
bt run --config <your.yaml>
bt report --run <run_id>
bt verify --run <run_id>        # R1 摘要一致性（含基准项）
```

注意事项（19 号 §24.6 接入摩擦清单）：

- 策略构造参数经 `inspect.signature` 精确匹配注入；**规则参数字面量不得写入
  YAML**（规则经 `rules` 注入——单一真源 H2）。
- 策略声明 `rules` / `liq_series` / `index_universe` 形参时，装配期自动注入
  规则源 / LIQ 预计算序列 / 月度成分映射。
- 配置 `report.benchmark` 时：装配期即校验（形制 + 区间覆盖，避免长区间白跑），
  基准号形如 `000300.SH`。
- 产物默认落 `代码\\回测产物\\runs\\`（`paths.py`），不污染 btf 树。
"""
