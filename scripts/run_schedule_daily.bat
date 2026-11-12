@echo off
rem P1-1（§435）：周期调度每日入口 —— 只调用 CLI，不直接调模块
rem ⛔ 解释器必须用项目 venv（系统 python 无 pandas）
chcp 65001 >nul
setlocal
set "ROOT=D:\量化策略\代码"
set "PY=%ROOT%\_venv\Scripts\python.exe"
set "LOGDIR=%ROOT%\_scratch\schedule_logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "DAY=%%i"
cd /d "%ROOT%"
"%PY%" main.py schedule --apply >> "%LOGDIR%\%DAY%.log" 2>&1
exit /b %ERRORLEVEL%
