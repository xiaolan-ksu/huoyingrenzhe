@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem Prefer the bundled interpreter (works even if python is not in PATH)
set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -u main.py

echo.
echo [Program exited] Press any key to close this window.
pause >nul
