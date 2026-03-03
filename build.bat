@echo off
REM Short alias for build_executable.bat
REM Usage: build.bat

setlocal
call "%~dp0build_executable.bat" %*
set "EC=%errorlevel%"
endlocal & exit /b %EC%

