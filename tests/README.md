# tests\ — 主工程单元测试

> 事实锚点：2026-11-21 ｜ 测试文件：16 ｜ 框架：pytest（btf 子系统有独立测试集，见 `btf\tests\`）

## 分组清单

### 断点与落盘
| 文件 | 守卫内容 |
|---|---|
| `test_checkpoint_registry.py` | 断点登记门面（只读接口，扫描自检，域解析） |
| `test_checkpoint_timestamps.py` | 断点时间戳：旧文件兼容 + 新键写入 |
| `test_atomic_write_guard.py` | 落盘原子化守卫（临时文件 + replace） |
| `test_fail_not_mark_done.py` | **fail 不得标记完成**（防断点丢失导致重复拉取） |

### 调度
| 文件 | 守卫内容 |
|---|---|
| `test_schedule_config.py` | 调度频率表覆盖率与到期判定 |
| `test_schedule_runner_once.py` | `once` 断点语义 + 估算器参数序 / 预算 / 刷新拒绝 |
| `test_schedule_release_day.py` | `FREQ_DAYS` 日历月对齐（双条件） |

### 新鲜度与会话
| 文件 | 守卫内容 |
|---|---|
| `test_freshness_forward_looking.py` | 前置日历豁免、总数同源、豁免垫底 |
| `test_freshness_pending_refresh.py` | 「待补拉」判定档：只改写「黄 ∧ 命中登记」，strict 不受染 |

### 配置、路径与 CLI
| 文件 | 守卫内容 |
|---|---|
| `test_paths.py` | 三库路径 smoke |
| `test_config_api.py` | 配置公开面 smoke（`config\` 包 re-export） |
| `test_cli.py` | CLI 入口 smoke |
| `test_key_narrow_ack.py` | `KEY_NARROW_ACK` 显式裁定与增长护栏 |

### 数据契约
| 文件 | 守卫内容 |
|---|---|
| `test_meta_close_and_fetch_ts.py` | `mark_fetched` 行数 + meta-close 纯函数 + 补表重建口径 |
| `test_share_float_no_shrink.py` | `share_float` 折叠反例固化（禁止缩小） |
| `test_structural_empty.py` | 「结构性空」守卫 |

## 运行方式

```bash
cd D:\量化策略\代码
_venv\Scripts\python.exe -m pytest tests -q
```

## 红线

测试是**契约**：断言中的数字与口径来自实证缺陷（注释里带批次号），不得为"让测试通过"而放宽断言；口径变更须先改文档再改测试。
