@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PREBUILT="
if exist ".local\runtime\manifest.json" set "PREBUILT=--use-prebuilt"
if exist ".venv\Scripts\python.exe" goto existing_python
py -3.12 --version >nul 2>&1
if errorlevel 1 goto python311
py -3.12 "scripts\prepare_local.py" --install %PREBUILT%
goto finished
:python311
py -3.11 "scripts\prepare_local.py" --install %PREBUILT%
goto finished
:existing_python
".venv\Scripts\python.exe" "scripts\prepare_local.py" --install %PREBUILT%
:finished
if errorlevel 1 (
  echo.
  echo 安装或构建未完成。请确认已安装 Python 3.11 或 3.12、Node.js 22 或 24，并查看上方提示。
  pause
  exit /b 1
)
echo.
echo 已准备完成。现在可以使用“开始使用.cmd”。
pause
