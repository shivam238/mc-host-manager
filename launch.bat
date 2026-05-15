@echo off
title MC Host Manager
echo.
echo   MC Host Manager
echo   =============================
echo.

set "PYTHON_CMD=python"
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"

%PYTHON_CMD% --version >nul 2>&1
if errorlevel 1 (
    echo   ERROR: Python install nahi hai!
    echo   Download: https://python.org/downloads
    echo   Install karte waqt CHECK karo "Add to PATH"
    pause & exit
)

echo   Starting on http://localhost:7842
echo.
echo   Band karne ke liye: Ctrl+C or window band karo
echo.

%PYTHON_CMD% -c "import requests" >nul 2>&1
if errorlevel 1 (
    echo   Installing required Python packages...
    %PYTHON_CMD% -m pip install --user requests
)

set "APP_PORT=7842"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $r = Invoke-WebRequest -Uri ('http://127.0.0.1:' + $env:APP_PORT + '/status') -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 (
    echo   App already running on http://localhost:%APP_PORT%
    start "" "http://localhost:%APP_PORT%"
    goto :eof
)

%PYTHON_CMD% "%~dp0host_manager.py"
pause
