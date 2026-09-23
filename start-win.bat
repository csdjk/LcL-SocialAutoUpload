@echo off
setlocal
chcp 65001 >nul
title social-auto-upload Web Launcher
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "VITE=%ROOT%sau_frontend\node_modules\.bin\vite.cmd"

if not exist "%PYTHON%" (
  echo Missing local Python environment: "%PYTHON%"
  echo Install the project environment before starting the Web UI.
  pause
  exit /b 1
)
if not exist "%VITE%" (
  echo Missing frontend dependencies. Run npm install in "%ROOT%sau_frontend".
  pause
  exit /b 1
)

pushd "%ROOT%"
"%PYTHON%" -X utf8 -c "import sau_backend"
if errorlevel 1 (
  echo Backend dependencies are incomplete. Install the Web dependencies in the local .venv.
  popd
  pause
  exit /b 1
)

pushd "db"
"%PYTHON%" -X utf8 createTable.py
if errorlevel 1 (
  echo Database initialization failed.
  popd
  popd
  pause
  exit /b 1
)
popd

if not exist "videoFile" mkdir "videoFile"
if not exist "cookiesFile" mkdir "cookiesFile"

echo Starting backend on http://127.0.0.1:5409 ...
start "SAU Backend" /D "%ROOT%" cmd /k ""%PYTHON%" -X utf8 sau_backend.py"
echo Starting UI on http://127.0.0.1:5173 ...
start "SAU Frontend" /D "%ROOT%sau_frontend" cmd /k "npm run dev -- --host 127.0.0.1"
echo The backend and UI logs are in the two new terminal windows.
popd
