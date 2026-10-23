@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

echo ============================================
echo   WeChat Work Hub - Build Windows EXE
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. Install Python 3.10+ first.
  pause
  exit /b 1
)

echo [1/4] Installing build tools (pyinstaller, pywebview)...
python -m pip install --upgrade pyinstaller pywebview -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo [ERROR] pip install failed. Check network or try another mirror.
  pause
  exit /b 1
)

echo [2/4] Cleaning old output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/4] Packaging (may take a few minutes)...
python -m PyInstaller --noconfirm --windowed --onedir --name "WeChatWorkHub" --paths "." app.py
if errorlevel 1 (
  echo [ERROR] PyInstaller failed. See messages above.
  pause
  exit /b 1
)

echo [4/4] Copying config / assets next to exe...
if not exist "dist\WeChatWorkHub\config" mkdir "dist\WeChatWorkHub\config"
xcopy /E /I /Y "config" "dist\WeChatWorkHub\config" >nul
xcopy /E /I /Y "web\assets" "dist\WeChatWorkHub\web\assets" >nul

echo.
echo   BUILD OK
echo   Run: dist\WeChatWorkHub\WeChatWorkHub.exe
echo.
echo   Put your .env next to the exe:  dist\WeChatWorkHub\.env
echo.
pause
exit /b 0
