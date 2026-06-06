@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

set "PROXY_ARGS="
choice /C YN /M "Use proxy?"
if errorlevel 2 set "PROXY_ARGS=--proxy """

"%PYTHON%" -m rusprofile_parser --headed %* %PROXY_ARGS%
echo.
echo Finished. Press any key to close this window.
pause >nul
