@echo off
cd /d "%~dp0"
title Void Crypto Trader - After Unzip Setup
echo ============================================
echo   VOID CRYPTO TRADER - ONE CLICK SETUP
echo ============================================
echo.
echo Windows cannot auto-run an exe just from unzipping
echo (that would be a security risk). This script does
echo the next step for you: install deps + optional exe.
echo.
echo [1] Install Python packages and open the app NOW
echo [2] Install packages + BUILD exe (takes a few minutes)
echo [3] Exit
echo.
choice /C 123 /N /M "Pick 1, 2, or 3: "
if errorlevel 3 goto end
if errorlevel 2 goto buildexe
if errorlevel 1 goto runapp

:runapp
echo.
echo Installing packages...
python -m pip install -q rich click requests python-dotenv pydantic matplotlib
if errorlevel 1 py -m pip install -q rich click requests python-dotenv pydantic matplotlib
echo Starting app...
python gui_app.py
if errorlevel 1 py gui_app.py
goto end

:buildexe
echo.
echo Installing packages + PyInstaller...
python -m pip install -q rich click requests python-dotenv pydantic matplotlib pyinstaller
if errorlevel 1 py -m pip install -q rich click requests python-dotenv pydantic matplotlib pyinstaller
echo Building exe (please wait)...
call build_exe.bat
echo.
echo If build succeeded, run:
echo   dist\VoidCryptoTrader.exe
echo.
explorer dist 2>nul
goto end

:end
pause
