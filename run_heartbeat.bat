@echo off
REM Runs one Moltbook heartbeat. Used by Windows Task Scheduler.
REM Works from any folder: it switches to the folder this .bat file is in.
cd /d "%~dp0"

REM Make Python print emoji safely when there is no console.
set PYTHONIOENCODING=utf-8

REM If you use a virtual environment, uncomment the next line:
REM call venv\Scripts\activate.bat

python heartbeat.py %* >> heartbeat_console.log 2>&1
