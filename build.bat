@echo off
REM One command full pipeline (deps + build + package).
REM Usage: build.bat

setlocal enabledelayedexpansion
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

echo [1/6] Python:
%PYTHON_CMD% -c "import sys; print(sys.executable)"
if errorlevel 1 (
  echo Error: Python not found. Install Python 3.11+ and retry.
  exit /b 1
)

echo [2/6] Ensuring build dependencies (pyinstaller, requests)
%PYTHON_CMD% -m pip install --upgrade pip >nul
%PYTHON_CMD% -m pip install pyinstaller requests >nul

echo [3/6] Cleaning old build artifacts
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist mc-host-manager.spec del /q mc-host-manager.spec
if exist host_manager.spec del /q host_manager.spec

echo [4/6] Building single executable
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
if errorlevel 1 exit /b 1

echo [5/6] Creating release package
if not exist release mkdir release
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMdd-HHmmss\")"') do set "STAMP=%%i"
set "PKG_DIR=release\mc-host-manager-windows-%STAMP%"
if exist "%PKG_DIR%" rmdir /s /q "%PKG_DIR%"
mkdir "%PKG_DIR%"
copy /y "dist\mc-host-manager.exe" "%PKG_DIR%\" >nul
copy /y "README.md" "%PKG_DIR%\" >nul
copy /y "SETUP.md" "%PKG_DIR%\" >nul
copy /y "LICENSE.md" "%PKG_DIR%\" >nul
set "ARCHIVE=release\mc-host-manager-windows-%STAMP%.zip"
powershell -NoProfile -Command "Compress-Archive -Path '%PKG_DIR%\*' -DestinationPath '%ARCHIVE%' -Force"

echo [6/6] Creating installer
%PYTHON_CMD% "%ROOT%make_single_file_installers.py" --windows-bin "%ROOT%dist\mc-host-manager.exe" --output "%ROOT%release"
if errorlevel 1 (
  echo Warning: installer generation failed.
)

echo.
echo Build complete:
echo   Executable: %ROOT%dist\mc-host-manager.exe
echo   Package:    %ROOT%%ARCHIVE%
echo   Installer:  %ROOT%release\mc-host-manager-installer-windows.bat
endlocal
