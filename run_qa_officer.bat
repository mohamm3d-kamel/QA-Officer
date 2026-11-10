@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHONIOENCODING=utf-8"

echo =============================================
echo        Starting QA Officer Assistant
echo =============================================

if not exist "%ROOT%backend\storage\evidence" mkdir "%ROOT%backend\storage\evidence"

set "NPM_CMD=npm.cmd"
where npm.cmd >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\nodejs\npm.cmd" set "NPM_CMD=C:\Program Files\nodejs\npm.cmd"
)

echo 1. Starting FastAPI Backend on http://localhost:8000...
start "QA Officer Backend" /D "%ROOT%backend" "%ROOT%venv\Scripts\python.exe" main.py

echo 2. Starting Vite Frontend on http://localhost:5173...
start "QA Officer Frontend" /D "%ROOT%frontend" "%NPM_CMD%" run dev -- --host 0.0.0.0

echo.
echo QA Officer is starting!
echo   * Frontend: http://localhost:5173
echo   * Backend API: http://localhost:8000
echo   * API Docs (Swagger): http://localhost:8000/docs
echo.
endlocal
