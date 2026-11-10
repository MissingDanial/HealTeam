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
  echo 请先停止服务，再将备份 ZIP 拖到本文件上。
  echo 默认不覆盖现有数据库。需要替换时，在命令行显式追加 --replace。
  echo 替换前会自动保存旧库安全备份。
  pause
  exit /b 1
)
if "%~2"=="--replace" (
  ".venv\Scripts\python.exe" scripts\manage_data.py restore "%~1" --replace --human
) else (
  ".venv\Scripts\python.exe" scripts\manage_data.py restore "%~1" --human
)
set "healteam_exit=%errorlevel%"
pause
exit /b %healteam_exit%
