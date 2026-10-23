@echo off
rem Stop the Chat Digest console (background service on port 5710).
setlocal
cd /d "%~dp0"

set "KILLED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5710" ^| findstr "LISTENING"') do (
    taskkill /PID %%P /F >nul 2>&1 && set "KILLED=1"
)

netstat -ano | findstr ":5710" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [X] Still running - close it manually in Task Manager.
    pause
    exit /b 1
)

if "%KILLED%"=="1" (
    echo [OK] Console stopped.
) else (
    echo [i] Console was not running.
)
pause
exit /b 0
