@echo off
rem P1-1 (section 435): daily schedule entry - CLI only, never call module directly.
rem
rem This file is intentionally PURE ASCII. The repo root path contains CJK
rem characters, and cmd.exe resolves a .bat by the ACTIVE CODE PAGE, so a file
rem holding CJK bytes breaks as soon as the code page and the file encoding
rem disagree (observed: Task Scheduler LastResult=3, "path not found").
rem Do NOT hardcode the path here and do NOT switch to UTF-8: derive it from
rem the script location with %~dp0 instead, so the bytes stay ASCII forever.
rem Line endings must stay CRLF.
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%"
set "PY=%ROOT%\_venv\Scripts\python.exe"
set "LOGDIR=%ROOT%\_scratch\schedule_logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "DAY=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%"
"%PY%" main.py schedule --apply >> "%LOGDIR%\%DAY%.log" 2>&1
exit /b %ERRORLEVEL%
