# scripts\ — 调度与补数批处理入口

> 事实锚点：2026-11-21 ｜ 文件数：6 ｜ 定位：**Windows 计划任务 / 手工批处理的直接入口**（不承载业务逻辑，逻辑在 `fetch\` 与 `tools\`）

## 模块清单

| 文件 | 作用 | 对外入口 |
|---|---|---|
| `run_schedule_daily.bat` | `CC_ScheduleDaily` 计划任务入口 → `main.py schedule --apply`（周/月/季表 + 日备） | `schtasks` 17:30 每日 |
| `install_schedule_task.ps1` | 注册上述计划任务（**Trae 不自行执行 `/Create`**，须人工运行） | 人工一次性 |
| `run_backfill_batches.py` | 分批补数 driver：每批 N 任务 + 批后自检，出错即停（避免「断点 +1,040 但只落 4 个指数」） | `python scripts/run_backfill_batches.py` |
| `check_backfill_coverage.py` | 补数覆盖自检：断点键数守恒 + 数据行数不回退（引擎外兜底） | `python scripts/check_backfill_coverage.py` |
| `clean_fake_checkpoint.py` | 清理「断点说完成但数据 0 行」的假完成断点（默认 dry-run） | `python scripts/clean_fake_checkpoint.py [--apply]` |
| `verify_landed.py` | 补拉落盘校验：清单内每个码是否**真的**进了库（不采信「成功=N」） | `python scripts/verify_landed.py` |

## 与其他目录的关系

- **调用** `main.py` / `fetch.*` / `tools.*`，自身不含取数或落盘实现。
- 与 `tools\` 的分工：`scripts\` = **批处理/调度入口**（可被计划任务直接调起）；`tools\` = **可被 CLI 调用的运维命令**。

## 红线

1. `install_schedule_task.ps1` 中的 `/Create` **只能人工执行**（Trae/自动化不得代跑）。
2. 四个批处理脚本默认均为 **dry-run / 只读自检**；写入须显式 `--apply`。
3. 计划任务时刻不得与既有任务冲突（现存 17:30 / 18:00 / 19:00 / 20:00 四档，见根 README）。
