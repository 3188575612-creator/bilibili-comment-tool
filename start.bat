@echo off
echo ==================================================
echo    Bilibili Comment Tool
echo ==================================================
echo.

cd /d "%~dp0"

echo Checking Python...
"C:\Users\zqstx\AppData\Local\Programs\Python\Python311\python.exe" --version
if %errorlevel% neq 0 (
    echo Python not found!
    pause
    exit /b 1
)

echo.
echo Installing dependencies...
"C:\Users\zqstx\AppData\Local\Programs\Python\Python311\python.exe" -m pip install flask bilibili-api-python httpx

echo.
echo ==================================================
echo Starting server...
echo Please open browser: http://localhost:5000
echo Press Ctrl+C to stop server
echo ==================================================
echo.

"C:\Users\zqstx\AppData\Local\Programs\Python\Python311\python.exe" app.py

echo.
echo Server stopped. Press any key to close...
pause >nul
