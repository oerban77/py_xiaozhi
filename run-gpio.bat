@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"
"%PYTHON_EXE%" "%SCRIPT_DIR%main.py" --gpio %*
