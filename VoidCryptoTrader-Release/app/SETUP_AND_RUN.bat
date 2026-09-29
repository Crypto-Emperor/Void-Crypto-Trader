@echo off
cd /d "%~dp0"
title Void Crypto Trader
echo Installing packages...
python -m pip install -q rich click requests python-dotenv pydantic matplotlib
if errorlevel 1 (
  py -m pip install -q rich click requests python-dotenv pydantic matplotlib
  py gui_app.py
  goto end
)
python gui_app.py
:end
if errorlevel 1 pause
