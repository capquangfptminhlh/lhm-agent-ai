@echo off
setlocal
cd /d "%~dp0"
echo.
echo   LHM AGENT COMPUTER - CODEX-LIKE LOCAL RUNNER
echo   ===========================================
echo   Runs Docker sandbox, API and background worker.
echo.
if not exist ".env" (
  echo [ERROR] Missing .env
  echo Copy .env.example to .env and configure LHM_COMPUTER_RUNNER_TOKEN.
  exit /b 1
)
where docker >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Docker Desktop is not installed or not on PATH.
  echo Install Docker Desktop and ensure Linux containers mode is enabled.
  exit /b 1
)
docker info >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Docker Desktop engine is not running.
  exit /b 1
)
echo [INFO] Starting API, worker and isolated Computer Runner...
docker compose --profile computer up --build -d
if errorlevel 1 (
  echo [ERROR] Container startup failed.
  echo Run: docker compose --profile computer logs --tail=100
  exit /b 1
)
echo [OK] Computer runtime containers started.
echo Open http://127.0.0.1:7770/computer to assign your Agent a goal.
echo.
start "" "http://127.0.0.1:7770/computer"
exit /b 0
