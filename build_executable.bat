@echo off
REM Build one-file Windows executable using PyInstaller.
REM Run this from project root on Windows: build_executable.bat

setlocal enabledelayedexpansion

set "PYTHON_CMD="
if exist "venv\Scripts\python.exe" (
  set "PYTHON_CMD=venv\Scripts\python.exe"
) else if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
) else if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
  set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
) else (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
  ) else (
    set "PYTHON_CMD=python"
  )
)

echo [1/5] Using Python:
%PYTHON_CMD% -c "import sys; print(sys.executable)"
if errorlevel 1 (
  echo Error: Python not found. Install Python 3.11+ and retry.
  exit /b 1
)

echo [2/5] Verifying project files
if not exist "host_manager.py" (
  echo Error: host_manager.py not found.
  exit /b 1
)
if not exist "ui.html" (
  echo Error: ui.html not found.
  exit /b 1
)
if not exist "utils" (
  echo Error: utils folder not found.
  exit /b 1
)

echo [3/5] Checking build deps (pyinstaller, requests)
%PYTHON_CMD% -c "import PyInstaller, requests" >nul 2>&1
if errorlevel 1 (
  echo Missing dependency detected. Attempting install...
  %PYTHON_CMD% -m pip install --upgrade pip
  %PYTHON_CMD% -m pip install pyinstaller requests
)
%PYTHON_CMD% -c "import PyInstaller, requests" >nul 2>&1
if errorlevel 1 (
  echo Error: required packages not available: pyinstaller, requests.
  echo Install them manually and run this script again.
  exit /b 1
)

echo [4/5] Cleaning old build artifacts
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist mc-host-manager.spec del /q mc-host-manager.spec
if exist host_manager.spec del /q host_manager.spec

echo [5/5] Building single executable
if exist "bin" (
  %PYTHON_CMD% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --name mc-host-manager ^
    --add-data "ui.html;." ^
    --add-data "utils;utils" ^
    --add-data "bin;bin" ^
    --hidden-import zipfile ^
    host_manager.py
) else (
  %PYTHON_CMD% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --name mc-host-manager ^
    --add-data "ui.html;." ^
    --add-data "utils;utils" ^
    --hidden-import zipfile ^
    host_manager.py
)

echo.
echo Build complete:
echo   dist\mc-host-manager.exe
echo.
echo Tip: run dist\mc-host-manager.exe then open http://localhost:7842
endlocal
