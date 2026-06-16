@echo off
REM Start FastAPI server with HTTPS/SSL support
REM This script starts the trading application with SSL certificates

echo.
echo ========================================
echo   AshAlgo Trading - HTTPS Server
echo ========================================
echo.

REM Activate virtual environment
echo [1/3] Activating virtual environment...
call myvenv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
)

REM Check if SSL certificates exist
echo [2/3] Checking SSL certificates...
if not exist ssl_cert.pem (
    echo.
    echo WARNING: SSL certificate not found!
    echo Generating self-signed certificate...
    python generate_ssl_cert.py
    if errorlevel 1 (
        echo ERROR: Failed to generate SSL certificates
        pause
        exit /b 1
    )
)

REM Start server with HTTPS
echo [3/3] Starting HTTPS server...
echo.
echo Server will be accessible at:
echo   - https://localhost:8000
echo   - https://134.195.138.91:8000
echo.
echo Webhook URL for Zerodha:
echo   https://134.195.138.91:8000/webhook/chartink?user_id=1
echo.
echo Press Ctrl+C to stop the server
echo ========================================
echo.

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=ssl_key.pem --ssl-certfile=ssl_cert.pem

pause
