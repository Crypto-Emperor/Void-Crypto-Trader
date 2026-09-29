@echo off
title Void Crypto Trader
cd /d "%~dp0"
if not exist "app\gui_app.py" (
  echo ERROR: app folder missing.
  pause
  exit /b 1
)
cd app
echo Starting Void Crypto Trader...
python -c "import matplotlib,rich,click,requests,dotenv,pydantic" 1>nul 2>nul
if errorlevel 1 (
  echo Installing packages...
  python -m pip install -q rich click requests python-dotenv pydantic matplotlib
  if errorlevel 1 (
    py -m pip install -q rich click requests python-dotenv pydantic matplotlib
    py gui_app.py
    goto end
  )
)
python gui_app.py
if errorlevel 1 py gui_app.py
:end
if errorlevel 1 pause
