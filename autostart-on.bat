@echo off
cd /d "%~dp0"
set "SU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
copy /y "%~dp0tools\v2o-startup.vbs" "%SU%\Video2Obsidian.vbs" > logs\autostart.log 2>&1
if exist "%SU%\Video2Obsidian.vbs" (echo AUTOSTART_OK) else (echo AUTOSTART_FAIL & type logs\autostart.log)
echo Starting now in background...
wscript "%~dp0start-hidden.vbs"
timeout /t 5 >nul
