@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "LOG=%~dp0install.log"
echo Installing... this can take 5-15 minutes. Log: install.log
call :main > "%LOG%" 2>&1
type "%LOG%" | findstr /b /c:"INSTALL_"
echo.
echo Done. You can close this window. Then double-click start.bat
pause
exit /b

:main
echo [%date% %time%] install start
set "PY="
py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
if not defined PY py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY if exist "%USERPROFILE%\miniconda3\python.exe" set "PY=%USERPROFILE%\miniconda3\python.exe"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo INSTALL_FAIL no python found
  exit /b 1
)
echo using: %PY%
%PY% -c "import sys,platform;print(sys.version, platform.machine())"
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo INSTALL_FAIL venv
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo INSTALL_FAIL pip
  exit /b 1
)
echo downloading whisper model...
".venv\Scripts\python.exe" -c "import json;from faster_whisper import WhisperModel;m=json.load(open('config.json',encoding='utf-8'))['whisper_model'];WhisperModel(m,device='cpu',compute_type='int8');print('WHISPER_OK',m)"
if errorlevel 1 (
  echo INSTALL_FAIL whisper
  exit /b 1
)
echo INSTALL_OK
exit /b 0
