# push/ — 微信推送子包（PushPlus 发送、限频、聚合、冷却）

> 事实锚点：2026-11-21 ｜ 模块数：2 ｜ 入口：`python main.py push-test` → `cli.misc_cmds.cmd_push_test` → `push.pushplus.run_push`

## 职责

- 基于 PushPlus 官方 API 发送微信消息：`channel` 支持 `wechat`（默认）/ `clawbot` 等，`template` 支持 `txt`/`markdown`/`html`/`json`。
- 在客户端内实现配额与防抖：200 条/天、5 条/分钟、相同内容 3 条/小时，内容上限 20000 字符、标题 100 字符。
- 支持多条信号聚合为一条推送（省额度）与冷却去重（同一信号 N 分钟内不重复发）。

## 关键模块

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 延迟导入门面：`PushPlus` 按需导入；`run_scan` / `run_report` 已随链路归档，访问即抛 `AttributeError` 并指向 `_archive/` |
| `pushplus.py` | `PushPlus` 客户端（`send`/`test`/`get_status`、额度与冷却控制）、`run_push()` 统一发送入口、`main()` 独立 CLI；token 默认取 `config.PUSHPLUS_TOKEN` |

## 与其他包的关系

- 依赖方向：`push` → `common.jsonio`（配额/状态持久化）、`push` → `config`（`PUSHPLUS_TOKEN`/`PUSHPLUS_CHANNEL`/`PUSHPLUS_TEMPLATE`/`TEMP_DIR`）。
- 被依赖：`cli.misc_cmds`（`push-test` 命令）与 `fetch.daily_update`（日更告警）在函数内延迟导入本包。
- 禁止反向依赖：`push` 不得 import `cli`/`fetch`/`tools`/`alt`（否则与日更告警形成循环）。

## 红线

- token 只能经 `config.tokens` 读取（环境变量 > `.env`），代码内不得出现明文；无 token 时 `run_push` 直接抛 `ValueError`，不得静默降级。
- 返回码 `900`（当日额度用完/被封）必须立即停止，不得重试；`903`/`905` 分别按 token 无效、未实名处理。
- 免费版三档限频（200/天、5/分钟、同内容 3/小时）不得放宽；发送前先查本地计数。
- `push/__init__.py` 的 `run_scan` / `run_report` 属归档入口，只能抛 `AttributeError`，禁止恢复实现。
- 包级 `import push` 不得产生网络请求或日志副作用（`PushPlus` 仅在首次访问时延迟导入）。
