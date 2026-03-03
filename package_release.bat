@echo off
REM Build and package a Windows release archive.
REM Usage: package_release.bat

setlocal enabledelayedexpansion
set "ROOT=%~dp0"
cd /d "%ROOT%"

call "%ROOT%build.bat"
if errorlevel 1 exit /b 1

if not exist release mkdir release

set "PYTHON_CMD=python"
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"

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

%PYTHON_CMD% "%ROOT%make_single_file_installers.py" --windows-bin "%ROOT%dist\mc-host-manager.exe" --output "%ROOT%release"
if errorlevel 1 (
  echo Warning: installer generation failed.
)

echo Release package created: %ROOT%%ARCHIVE%
echo Single-file installer: %ROOT%release\mc-host-manager-installer-windows.bat
endlocal
