@echo off
setlocal
cd /d "%~dp0"
if not exist .env (
  copy /y .env.example .env >nul
  echo [INFO] Da tao .env. Mo file nay de dien DEEPSEEK_API_KEY neu muon chay AI that.
)
if not exist .venv\Scripts\python.exe (
  py -m venv .venv
  if errorlevel 1 ( echo Python 3 not found & pause & exit /b 1 )
)
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if errorlevel 1 ( echo Installation failed & pause & exit /b 1 )
echo [LHM] Van phong AI: http://127.0.0.1:7770/office
echo [LHM] Dashboard:   http://127.0.0.1:7770/dashboard
echo [LHM] API docs:    http://127.0.0.1:7770/docs
echo [NOTE] De xu ly hang doi, hay mo them WORKER_WINDOWS.bat
start "" "http://127.0.0.1:7770/office"
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 7770
pause