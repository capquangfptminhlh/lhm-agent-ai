@echo off
setlocal
cd /d "%~dp0"
if not exist .env copy /y .env.example .env >nul
if not exist .venv\Scripts\python.exe py -m venv .venv
if not exist .venv\Scripts\python.exe ( echo Can cai Python 3.11+ & pause & exit /b 1 )
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if errorlevel 1 ( echo Khong cai duoc thu vien Python & pause & exit /b 1 )
start "LHM Agent OS Server" /D "%~dp0" cmd /k ".venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 7770"
start "LHM AI Worker" /D "%~dp0" cmd /k ".venv\Scripts\python.exe worker.py"
echo Da khoi dong backend va worker LHM trong 2 cua so.
echo Office: http://127.0.0.1:7770/office
echo Dashboard: http://127.0.0.1:7770/dashboard
echo Tool Hub: http://127.0.0.1:7770/tool-hub
echo.
echo Neu muon 3D Hermes3D, chay START_HERMES3D_WINDOWS.bat sau khi cai Git + Node.
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:7770/office"