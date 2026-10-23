@echo off
rem Start Chat Digest console as a windowless background service.
rem Safe to run many times: exits quietly if already running.
setlocal EnableExtensions
cd /d "%~dp0"

rem ---------- locate a usable Python with dependencies ----------
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
if not defined PY exit /b 1
"%PY%" -c "import yaml" >nul 2>&1
if errorlevel 1 exit /b 1
set "PYW=%PY:python.exe=pythonw.exe%"

rem ---------- optional: refresh local data ----------
rem   Runs only what you configured; this script never decrypts or downloads
rem   anything itself. WX_REFRESH_SCRIPT or a local_refresh.bat next to this file.
if defined WX_REFRESH_SCRIPT if exist "%WX_REFRESH_SCRIPT%" "%PY%" "%WX_REFRESH_SCRIPT%" auto >nul 2>&1
if exist "%~dp0local_refresh.bat" call "%~dp0local_refresh.bat" >nul 2>&1

rem ---------- already listening? then do nothing ----------
netstat -ano | findstr ":5710" | findstr "LISTENING" >nul
if not errorlevel 1 exit /b 0

rem ---------- launch hidden ----------
if not exist "%PYW%" exit /b 1
start "" "%PYW%" "%~dp0tools\serve_hidden.py"
exit /b 0
