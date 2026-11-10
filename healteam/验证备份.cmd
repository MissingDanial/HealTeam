@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先双击“安装与构建.cmd”，安装本机运行所需文件。
  pause
  exit /b 1
)
if "%~1"=="" (
  echo 请将备份 ZIP 文件拖到本文件上，以验证备份完整性。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\manage_data.py verify "%~1" --human
set "healteam_exit=%errorlevel%"
pause
exit /b %healteam_exit%
