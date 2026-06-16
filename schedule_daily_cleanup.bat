@echo off
REM Daily Cleanup Scheduler for Windows
REM This script runs the daily cleanup at 8 AM IST

cd /d "%~dp0"

REM Activate virtual environment if it exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Load environment variables
if exist ".env" (
    for /f "delims=" %%i in (.env) do set %%i
)

REM Run cleanup script
echo Running daily cleanup...
python app\daily_cleanup.py

REM Log completion
echo Cleanup completed at %date% %time%
