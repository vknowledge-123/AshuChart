@echo off
setlocal DisableDelayedExpansion

REM Check for permissions
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"

REM If error flag set, we do not have admin.
if '%errorlevel%' NEQ '0' (
    echo Requesting administrative privileges...
    goto UACPrompt
) else ( goto gotAdmin )

:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~s0", "", "", "runas", 1 >> "%temp%\getadmin.vbs"
    "%temp%\getadmin.vbs"
    exit /B

:gotAdmin
    if exist "%temp%\getadmin.vbs" ( del "%temp%\getadmin.vbs" )
    pushd "%CD%"
    CD /D "%~dp0"

echo ===================================================
echo   AlgoEdge Daily Auto-Reset Setup
echo ===================================================
echo.
echo This script will schedule the daily cleanup task to run automatically
echo every morning at 08:00 AM.
echo.

set "TASK_NAME=AlgoEdgeDailyCleanup"
set "SCRIPT_PATH=%~dp0schedule_daily_cleanup.bat"

echo Task Name: %TASK_NAME%
echo Script Path: %SCRIPT_PATH%
echo.

rem Check if task already exists
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo Task already exists. Deleting execution...
    schtasks /delete /tn "%TASK_NAME%" /f
)

rem Create the task
echo Creating scheduled task...
schtasks /create /tn "%TASK_NAME%" /tr "\"%SCRIPT_PATH%\"" /sc daily /st 08:00 /rl HIGHEST /f

if %errorlevel% equ 0 (
    echo.
    echo [SUCCESS] Task scheduled successfully! 
    echo The cleanup will now run automatically every day at 08:00 AM.
    echo.
    echo You can close this window now.
    pause
    exit /b 0
) else (
    echo.
    echo [ERROR] Failed to schedule task even with admin rights.
    pause
    exit /b 1
)
