@echo off
REM One-click full pipeline: dependency check + build + release package.
REM Usage: release.bat

setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

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

echo [release 1/3] Python:
%PYTHON_CMD% -c "import sys; print(sys.executable)"
if errorlevel 1 (
  echo Error: Python not found. Install Python 3.11+ and retry.
  exit /b 1
)

echo [release 2/3] Ensuring packaging dependencies
%PYTHON_CMD% -m pip install --upgrade pip >nul
%PYTHON_CMD% -m pip install pyinstaller requests >nul

echo [release 3/3] Building and packaging
call "%ROOT%package_release.bat" %*
set "EC=%errorlevel%"
endlocal & exit /b %EC%
