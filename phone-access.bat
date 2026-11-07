@echo off
cd /d "%~dp0"
set "TS=C:\Program Files\Tailscale\tailscale.exe"
if not exist "%TS%" set "TS=C:\Program Files (x86)\Tailscale\tailscale.exe"
if not exist "%TS%" (echo Tailscale not found in Program Files & pause & exit /b 1)
echo Using "%TS%"
"%TS%" serve --bg --https=443 http://127.0.0.1:8791 > logs\phone-access.log 2>&1
"%TS%" serve status >> logs\phone-access.log 2>&1
type logs\phone-access.log
echo.
echo If it says Access denied: right-click this file and choose "Run as administrator".
pause
