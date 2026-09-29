@echo off
title Build Void Crypto Trader EXE
cd /d "%~dp0app"
echo ============================================
echo  Building real VoidCryptoTrader.exe
echo  This takes 2-5 minutes. Keep window open.
echo ============================================
echo.

python -m pip install -q rich click requests python-dotenv pydantic matplotlib pyinstaller
if errorlevel 1 (
  py -m pip install -q rich click requests python-dotenv pydantic matplotlib pyinstaller
  set PY=py
) else (
  set PY=python
)

echo Running PyInstaller (one-file exe)...
%PY% -m PyInstaller --noconfirm --clean --windowed --onefile ^
  --name VoidCryptoTrader ^
  --paths "." ^
  --hidden-import=matplotlib ^
  --hidden-import=PIL ^
  --hidden-import=portfolio ^
  --hidden-import=accounts ^
  --hidden-import=prices ^
  --hidden-import=config ^
  --hidden-import=equity_history ^
  --hidden-import=providers ^
  --hidden-import=free_api ^
  --hidden-import=fomo_tokens ^
  --hidden-import=pnl_rates ^
  --hidden-import=auto_trader ^
  --hidden-import=dotenv ^
  --hidden-import=strategies ^
  --hidden-import=strategies.top_accounts_copy ^
  --hidden-import=strategies.base ^
  --collect-submodules=matplotlib ^
  gui_app.py

if not exist "dist\VoidCryptoTrader.exe" (
  echo BUILD FAILED - no exe produced.
  pause
  exit /b 1
)

cd /d "%~dp0"
if not exist "VoidCryptoTrader-App" mkdir "VoidCryptoTrader-App"
copy /Y "app\dist\VoidCryptoTrader.exe" "VoidCryptoTrader-App\VoidCryptoTrader.exe" >nul
copy /Y "LICENSE.txt" "VoidCryptoTrader-App\LICENSE.txt" >nul
copy /Y "README.txt" "VoidCryptoTrader-App\README.txt" >nul

REM data next to exe so settings/keys work
if not exist "VoidCryptoTrader-App\keys" mkdir "VoidCryptoTrader-App\keys"
xcopy /E /I /Y "app\keys" "VoidCryptoTrader-App\keys" >nul 2>&1
copy /Y "app\.env" "VoidCryptoTrader-App\.env" >nul 2>&1
copy /Y "app\accounts.json" "VoidCryptoTrader-App\accounts.json" >nul 2>&1
xcopy /E /I /Y "app\strategies" "VoidCryptoTrader-App\strategies" >nul 2>&1
copy /Y "app\*.py" "VoidCryptoTrader-App\" >nul 2>&1

echo.
echo ============================================
echo  DONE - your app folder:
echo    VoidCryptoTrader-App\
echo      VoidCryptoTrader.exe   ^<-- this is the app
echo      README.txt
echo      LICENSE.txt
echo ============================================
echo.
explorer "VoidCryptoTrader-App"
pause
