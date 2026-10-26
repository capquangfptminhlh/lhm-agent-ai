@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Hay chay START_WINDOWS.bat truoc.
  pause
  exit /b 1
)
echo [LHM] Worker doc cau hinh tu file .env. Neu chua co DeepSeek key, worker se cho.
.venv\Scripts\python.exe worker.py
pause