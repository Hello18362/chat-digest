@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ==========================================================
rem   Chat Digest - one-click launcher
rem ==========================================================
echo ==============================================
echo   Chat Digest - one-click launcher
echo ==============================================
echo.

rem ---------- 1. locate a usable Python with dependencies ----------
rem   Prefer an interpreter that already has PyYAML installed, so a stray
rem   interpreter earlier on PATH does not win over the configured one.
set "PY="
set "PY_ANY="
for %%C in ("py -3" "python") do (
    for /f "delims=" %%I in ('%%~C -c "import sys;print(sys.executable)" 2^>nul') do (
        if not defined PY_ANY set "PY_ANY=%%I"
        if not defined PY (
            "%%I" -c "import yaml" >nul 2>&1 && set "PY=%%I"
        )
    )
)
if not defined PY if defined PY_ANY set "PY=%PY_ANY%"
if not defined PY for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python310\python.exe"
) do if not defined PY if exist %%D set "PY=%%~D"

if not defined PY (
    echo [X] Python 3.10+ not found.
    echo     Install it from https://www.python.org/downloads/ and run this again.
    echo.
    pause
    exit /b 1
)
set "PYW=%PY:python.exe=pythonw.exe%"
echo [0/3] Python: %PY%
echo.

rem ---------- 1.5 dependencies ----------
"%PY%" -c "import yaml" >nul 2>&1
if errorlevel 1 (
    echo [X] Dependencies are missing for this interpreter:
    echo     %PY%
    echo.
    echo     Run:  "%PY%" -m pip install -e .
    echo.
    pause
    exit /b 1
)

rem ---------- 2. optional: refresh local data ----------
rem   Runs only what you configured; this script never decrypts or downloads
rem   anything itself. Two ways to plug in a refresher:
rem     * set WX_REFRESH_SCRIPT=C:\path\to\script.py
rem     * drop a local_refresh.bat next to this file (git-ignored)
echo [1/3] Refreshing local data (optional) ...
if defined WX_REFRESH_SCRIPT if exist "%WX_REFRESH_SCRIPT%" "%PY%" "%WX_REFRESH_SCRIPT%" auto >nul 2>&1
if exist "%~dp0local_refresh.bat" call "%~dp0local_refresh.bat" >nul 2>&1
echo       done.
echo.

rem ---------- 3. start the console as a background service ----------
echo [2/3] Starting console (http://127.0.0.1:5710) ...
netstat -ano | findstr ":5710" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo       already running, will reuse it
) else (
    if exist "%PYW%" (
        start "" "%PYW%" "%~dp0tools\serve_hidden.py"
        echo       started in background - no window to keep open
    ) else (
        start "WeChatWorkHub" /min cmd /c "cd /d "%~dp0" && "%PY%" main.py --serve"
        echo       started in a minimized window
    )
)

rem ---------- 4. open browser ----------
echo.
echo [3/3] Opening console page ...
ping -n 3 127.0.0.1 >nul
start http://127.0.0.1:5710

echo.
echo ==============================================
echo   Done! Console: http://127.0.0.1:5710
echo.
echo   The service runs in the background.
echo   This window can be closed safely.
echo   To stop it: double-click the stop-service script.
echo ==============================================
echo.
pause
