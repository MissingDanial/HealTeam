@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先双击“安装与构建.cmd”，安装本机运行所需文件。
  pause
  exit /b 1
)
echo 正在启动 HealTeam。此窗口保持开启，按 Ctrl+C 可以停止服务。
".venv\Scripts\python.exe" scripts\local_app.py start
set "healteam_exit=%errorlevel%"
if not "%healteam_exit%"=="0" (
  echo 启动未完成。可双击“运行检查.cmd”查看原因。
  pause
)
exit /b %healteam_exit%
