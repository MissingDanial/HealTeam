@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先双击“安装与构建.cmd”，安装本机运行所需文件。
  pause
  exit /b 1
)
echo 备份包含完整对话和人工复核。请先停止服务，并妥善保管备份文件。
".venv\Scripts\python.exe" scripts\manage_data.py backup --human
set "healteam_exit=%errorlevel%"
pause
exit /b %healteam_exit%
