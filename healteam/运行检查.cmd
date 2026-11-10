@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先双击“安装与构建.cmd”，安装本机运行所需文件。
  pause
  exit /b 1
)
echo 正在检查本机环境，不会调用付费模型。
".venv\Scripts\python.exe" scripts\local_app.py doctor --human
set "healteam_exit=%errorlevel%"
pause
exit /b %healteam_exit%
