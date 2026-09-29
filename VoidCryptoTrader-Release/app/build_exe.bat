@echo off
cd /d "%~dp0"
echo === Void Crypto Trader - build exe ===
python -m pip install -r requirements.txt pyinstaller matplotlib
echo.
echo Building...
python -m PyInstaller --noconfirm --windowed --name VoidCryptoTrader ^
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
  --hidden-import=dotenv ^
  --collect-submodules=matplotlib ^
  gui_app.py

echo.
echo Copying config next to exe so it can find keys/portfolio...
if not exist "dist" mkdir dist
copy /Y ".env" "dist\.env" >nul 2>&1
xcopy /E /I /Y "keys" "dist\keys" >nul 2>&1
copy /Y "accounts.json" "dist\accounts.json" >nul 2>&1
copy /Y "auto_trader.py" "dist\auto_trader.py" >nul 2>&1
copy /Y "*.py" "dist\" >nul 2>&1
xcopy /E /I /Y "strategies" "dist\strategies" >nul 2>&1

echo.
echo DONE.
echo Run this file:
echo   dist\VoidCryptoTrader.exe
echo.
echo If Windows blocks it: right-click exe - Properties - Unblock
echo Or skip exe and run:  python gui_app.py
echo.
pause
