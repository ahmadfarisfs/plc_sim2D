@echo off
rem Launches tools\main.py using the project's virtual environment.
rem Looks for ".venv" first, then "venv", falling back to system python.

setlocal
set "ROOT=%~dp0.."

if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "PY=%ROOT%\.venv\Scripts\python.exe"
) else if exist "%ROOT%\venv\Scripts\python.exe" (
    set "PY=%ROOT%\venv\Scripts\python.exe"
) else (
    set "PY=python"
)

"%PY%" "%ROOT%\tools\main.py"
if errorlevel 1 pause
