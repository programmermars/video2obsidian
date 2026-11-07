@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Not installed yet. Run install.bat first.
  pause
  exit /b 1
)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
rem 2026-11-04: runs in the background now, so closing a window never stops it.
rem For a visible console with live output (debugging): start.bat debug
if /i "%~1"=="debug" goto debug
curl -s -o nul -m 3 http://127.0.0.1:8791/api/status && goto open
echo Starting Video2Obsidian in the background...
wscript "%~dp0start-hidden.vbs"
for /l %%i in (1,1,30) do (
  curl -s -o nul -m 2 http://127.0.0.1:8791/api/status && goto open
  timeout /t 1 >nul
)
echo Did not start within 30s. Check logs\server.log, or run: start.bat debug
pause
exit /b 1
:open
start "" http://127.0.0.1:8791
exit /b 0
:debug
echo Video2Obsidian running at http://127.0.0.1:8791  (debug mode: close this window to stop)
".venv\Scripts\python.exe" app.py
