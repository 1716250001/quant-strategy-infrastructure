# P1-1（§435）：注册每日调度任务（17:30）
# ⛔ 本脚本只“准备”注册动作，须老大过一眼后手动执行：
#     powershell -ExecutionPolicy Bypass -File .\scripts\install_schedule_task.ps1
# ⛔ Trae 不自行执行 schtasks /Create
$ErrorActionPreference = "Stop"

$bat = "D:\量化策略\代码\scripts\run_schedule_daily.bat"
if (-not (Test-Path $bat)) { throw "缺少入口脚本: $bat" }

$task = "CC_ScheduleDaily"
schtasks /Create /SC DAILY /ST 17:30 /TN $task /TR "`"$bat`"" /F

Write-Host ""
Write-Host "已注册：$task（每天 17:30，入口 $bat）"
Write-Host "查看：  schtasks /Query /TN `"$task`" /V /FO LIST"
Write-Host "删除：  schtasks /Delete /TN `"$task`" /F"
