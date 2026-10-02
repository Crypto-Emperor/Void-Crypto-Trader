@echo off
setlocal EnableDelayedExpansion
title VOID CRYPTO TRADER
color 0B

rem ============================================================================
rem   VOID CRYPTO TRADER - the only launcher you need.
rem   Double-click this file. That is it. It will:
rem     1. Find Python (or open the installer for you)      [one time]
rem     2. Install dependencies                             [one time]
rem     3. Launch the VoidTrade web dashboard               [every time]
rem
rem   Advanced - pass a command after Void.bat:
rem     Void.bat trade    run the bot in this terminal
rem     Void.bat status   portfolio snapshot
rem     Void.bat gui      desktop chart terminal (Tkinter)
rem     Void.bat menu     interactive launcher menu
rem     Void.bat build    build the real VoidCryptoTrader.exe (PyInstaller)
rem ============================================================================

cd /d "%~dp0"
set "APP=%~dp0VoidCryptoTrader-Release\app"
set "REQ=%APP%\requirements.txt"
set "MARKER=%APP%\.void_deps_ok"
set "PY="

call :banner

rem ------------------------------------------------------------- find python --
call :find_python
if not defined PY call :install_python
if not defined PY goto :no_python

echo   [OK] Python found: !PY!
echo.

rem ----------------------------------------------------------- install deps --
if exist "%MARKER%" goto :deps_ok
echo   [*] Installing dependencies (first run only, about a minute)...
call %PY% -m pip install --quiet --disable-pip-version-check -r "%REQ%"
if errorlevel 1 (
    echo   [!] Pip failed - retrying with --user...
    call %PY% -m pip install --quiet --disable-pip-version-check --user -r "%REQ%"
    if errorlevel 1 goto :deps_fail
)
type nul > "%MARKER%"
:deps_ok

rem ---------------------------------------------------------------- dispatch --
set "CMD=%~1"
if "%CMD%"=="" set "CMD=web"

if /i "%CMD%"=="trade"  goto :run_trade
if /i "%CMD%"=="status" goto :run_status
if /i "%CMD%"=="gui"    goto :run_gui
if /i "%CMD%"=="menu"   goto :menu
if /i "%CMD%"=="build"  goto :build_exe
goto :run_web

:run_web
echo   [+] Launching the VoidTrade dashboard - your browser will open shortly.
echo       Keep this window open while trading. Close it to stop.
echo.
pushd "%APP%"
call %PY% voidtrade.py web
popd
goto :end_pause

:run_trade
pushd "%APP%"
call %PY% voidtrade.py trade
popd
goto :end_pause

:run_status
pushd "%APP%"
call %PY% voidtrade.py status
popd
goto :end_pause

:run_gui
pushd "%APP%"
call %PY% gui_app.py
popd
goto :end_pause

rem ------------------------------------------------------------------ menu ---
:menu
echo.
echo     ----------------------------------------------
echo      [1] Web dashboard         (recommended)
echo      [2] Trading desk          (terminal bot)
echo      [3] Portfolio status
echo      [4] Desktop charts        (Tkinter)
echo      [5] Build VoidCryptoTrader.exe
echo      [0] Exit
echo     ----------------------------------------------
choice /c 123450 /n /m "    Pick one: "
if errorlevel 6 goto :end
if errorlevel 5 goto :build_exe
if errorlevel 4 goto :run_gui
if errorlevel 3 goto :run_status
if errorlevel 2 goto :run_trade
goto :run_web

rem ------------------------------------------------------------ build .exe ---
:build_exe
echo   [*] Making sure PyInstaller is available...
call %PY% -m pip install --quiet --disable-pip-version-check pyinstaller
pushd "%APP%"
call %PY% -m PyInstaller --noconfirm --onefile --name VoidCryptoTrader void_launcher.py
popd
if errorlevel 1 (
    echo   [X] Build failed. Scroll up for the error message.
    goto :end_pause
)
copy /y "%APP%\dist\VoidCryptoTrader.exe" "%~dp0VoidCryptoTrader.exe" >nul
echo.
echo   [OK] Done! VoidCryptoTrader.exe now sits in this folder.
echo        Double-click it any time - no Python needed to RUN it.
goto :end_pause

rem ---------------------------------------------------------------- helpers --
:find_python
rem Prefer the Windows launcher "py -3", then plain "python".
where py >nul 2>&1
if errorlevel 1 goto :try_plain_python
py -3 -c "import sys" >nul 2>&1
if errorlevel 1 goto :try_plain_python
set "PY=py -3"
exit /b 0
:try_plain_python
where python >nul 2>&1
if errorlevel 1 exit /b 1
python -c "import sys" >nul 2>&1
if errorlevel 1 exit /b 1
set "PY=python"
exit /b 0

:install_python
echo   [!] Python was not found. Opening the official installer...
echo       IMPORTANT: tick "Add python.exe to PATH" during install,
echo       then double-click Void.bat again.
start "" "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
exit /b 1

:no_python
echo.
echo   [X] Could not find or set up Python. Follow the installer steps above.
pause
goto :end

:deps_fail
echo.
echo   [X] Dependency install failed. Common fixes:
echo       - Check your internet connection
echo       - Run:  python -m pip install --upgrade pip
echo       - Or install manually:  python -m pip install -r "%REQ%"
pause
goto :end

:banner
echo.
echo   ====  V O I D   T R A D E  ==============================
echo        paper-first crypto trader  ^|  freqtrade-inspired
echo   ==========================================================
echo.
exit /b 0

:end_pause
echo.
pause

:end
endlocal
exit /b 0
