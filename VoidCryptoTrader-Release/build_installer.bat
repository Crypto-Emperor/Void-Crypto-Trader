@echo off
setlocal EnableDelayedExpansion
title VOID CRYPTO TRADER - EXE + INSTALLER BUILDER
color 0B

rem ============================================================================
rem   One-click builder: makes VoidCryptoTrader.exe (PyInstaller) AND the
rem   Windows installer Setup exe (Inno Setup).
rem
rem   Run this file, then your installer is at:
rem       installer\Output\VoidCryptoTrader-Setup-1.4.1.exe
rem
rem   Needs once: Python 3 (python.org, "Add to PATH" ticked) and
rem               Inno Setup 6 (https://jrsoftware.org/isdl.php).
rem ============================================================================

cd /d "%~dp0"
set "APP=%~dp0app"
set "INS=%~dp0installer"
set "EXE=%INS%\files\VoidCryptoTrader.exe"
set "PY="

echo.
echo   ====  V O I D   B U I L D E R  =====================================
echo        PyInstaller exe  +  Inno Setup installer
echo   =====================================================================
echo.

rem ------------------------------------------------------------- find python --
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo   [X] Python not found. Install it from python.org first
    echo       (tick "Add python.exe to PATH"), then run this again.
    pause & exit /b 1
)
echo   [OK] Python: !PY!

rem ----------------------------------------------- install build deps --------
echo   [*] Making sure PyInstaller + app deps are installed (one time)...
call !PY! -m pip install --quiet --disable-pip-version-check pyinstaller&call !PY! -m pip install --quiet --disable-pip-version-check -r "%APP%\requirements.txt"
if errorlevel 1 (
    echo   [!] Retrying with --user...
    call !PY! -m pip install --quiet --disable-pip-version-check --user pyinstaller&call !PY! -m pip install --quiet --disable-pip-version-check --user -r "%APP%\requirements.txt"
)

rem ---------------------------------------------------- build the single exe --
echo   [*] Building VoidCryptoTrader.exe with PyInstaller (1-3 minutes)...
echo       (windowed build - double-clicking the exe opens the desktop app)
pushd "%APP%"
call !PY! -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name VoidCryptoTrader ^
    --icon "%INS%\VoidCryptoTrader.ico" ^
    --hidden-import dotenv --hidden-import rich --hidden-import click ^
    --hidden-import pydantic --hidden-import requests ^
    void_launcher.py
set "RC=!ERRORLEVEL!"
popd
if not "!RC!"=="0" (
    echo   [X] PyInstaller failed. Scroll up for the error.
    pause & exit /b 1
)
if not exist "%APP%\dist\VoidCryptoTrader.exe" (
    echo   [X] dist\VoidCryptoTrader.exe missing after build.
    pause & exit /b 1
)
if not exist "%INS%\files" mkdir "%INS%\files"
copy /y "%APP%\dist\VoidCryptoTrader.exe" "%EXE%" >nul
echo   [OK] Exe built and staged: !EXE!

rem ------------------------------------------------------- find Inno Setup ----
set "ISCC="
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
) do if exist %%P set "ISCC=%%~P"
if not defined ISCC where iscc >nul 2>&1
if not defined ISCC if not errorlevel 1 set "ISCC=iscc"

if not defined ISCC (
    echo.
    echo   [!] Inno Setup was not found - opening the official download...
    echo       Install it (defaults are fine), then run build_installer.bat again.
    echo       The exe IS already built, so you can also compile manually:
    echo         double-click installer\VoidCryptoTrader.iss
    start "" "https://jrsoftware.org/download.php/is.exe"
    pause & exit /b 1
)

rem ------------------------------------------------------- compile installer --
echo   [*] Compiling the installer with Inno Setup...
pushd "%INS%"
call !ISCC! VoidCryptoTrader.iss
set "RC=!ERRORLEVEL!"
popd
if not "!RC!"=="0" (
    echo   [X] Inno compile failed. Scroll up for the error line.
    pause & exit /b 1
)

echo.
echo   [OK] ALL DONE! Your distributable installer is here:
echo        %INS%\Output\VoidCryptoTrader-Setup-1.4.1.exe
echo.
echo        Give that file to anyone - it installs the desktop app with
echo        Start Menu + Desktop shortcuts. No Python or IP needed.
echo.
explorer "%INS%\Output"
pause
endlocal
exit /b 0
