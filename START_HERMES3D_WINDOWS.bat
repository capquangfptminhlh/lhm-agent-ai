@echo off
setlocal
cd /d "%~dp0"
where git >nul 2>&1 || (echo Can cai Git for Windows: https://git-scm.com/ & pause & exit /b 1)
where npm >nul 2>&1 || (echo Can cai Node.js 20+: https://nodejs.org/ & pause & exit /b 1)
if not exist "Hermes3D\package.json" (
  echo Dang tai giao dien Hermes3D MIT tu GitHub...
  git clone --depth 1 https://github.com/iamlukethedev/Hermes3D.git Hermes3D
  if errorlevel 1 ( echo Tai repo that bai, kiem tra mang. & pause & exit /b 1 )
)
pushd Hermes3D
if not exist node_modules (
  call npm install
  if errorlevel 1 ( echo npm install that bai & pause & exit /b 1 )
)
echo.
echo Truoc tien hay chay START_WINDOWS.bat + WORKER_WINDOWS.bat.
echo Khi Hermes3D mo, chon Custom backend, dia chi http://127.0.0.1:7770
echo LHM Agent OS endpoint: /health, /state, /registry, /v1/chat/completions
start "" "http://127.0.0.1:3000"
call npm run dev
popd
pause