@echo off
title VoidTrade
cd /d "%~dp0app"
where python >nul 2>&1 && set PY=python || set PY=py
%PY% -c "import flask" 1>nul 2>nul || %PY% -m pip install -q flask rich click requests python-dotenv pydantic
echo Opening VoidTrade in your browser...
%PY% voidtrade.py web
pause
