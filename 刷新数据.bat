@echo off
rem Refresh local chat data before generating a report.
rem
rem This launcher does NOT decrypt or download anything by itself - it only
rem runs the refresher you configured, so people using a different data source
rem (an exported SQLite snapshot, chatlog HTTP, ...) can skip it entirely.
rem
rem Three ways to plug one in, in priority order:
rem   1) set WX_REFRESH_SCRIPT=C:\path\to\your\refresher.py   (this window)
rem      setx WX_REFRESH_SCRIPT "C:\path\to\your\refresher.py" (persistent)
rem   2) drop a local_refresh.bat next to this file (it is git-ignored)
rem   3) do nothing - just export your database manually before generating
setlocal EnableExtensions
cd /d "%~dp0"

echo ==============================================
echo   Refresh local chat data
echo ==============================================
echo.

if not defined WX_REFRESH_SCRIPT if exist "%~dp0local_refresh.bat" (
    echo Running local_refresh.bat ...
    echo.
    call "%~dp0local_refresh.bat"
    echo.
    echo Done.
    pause
    exit /b 0
)

if not defined WX_REFRESH_SCRIPT (
    echo [i] No refresher configured - skipping.
    echo     Set WX_REFRESH_SCRIPT to your own refresh script, or drop a
    echo     local_refresh.bat next to this file.
    echo.
    pause
    exit /b 0
)
if not exist "%WX_REFRESH_SCRIPT%" (
    echo [X] WX_REFRESH_SCRIPT points to a missing file:
    echo     %WX_REFRESH_SCRIPT%
    echo.
    pause
    exit /b 1
)

set "PY="
for %%C in ("py -3" "python") do if not defined PY (
    for /f "delims=" %%I in ('%%~C -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY set "PY=%%I"
)
if not defined PY (
    echo [X] Python 3.10+ not found.
    pause
    exit /b 1
)

echo Running "%WX_REFRESH_SCRIPT%" ...
echo.
"%PY%" "%WX_REFRESH_SCRIPT%" auto
echo.
echo Done.
pause
