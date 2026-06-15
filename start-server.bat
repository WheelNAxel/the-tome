@echo off
title The Tome — Campaign Server
echo.
echo  ============================================
echo   THE TOME — D&D Campaign Server
echo  ============================================
echo.
echo   [1] Local only  (same WiFi)
echo   [2] Public link (anyone anywhere)
echo.
set /p MODE="   Choose 1 or 2: "
echo.

REM Add firewall rule once (needed for local mode too)
netsh advfirewall firewall show rule name="The Tome Server" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="The Tome Server" dir=in action=allow protocol=TCP localport=8000 >nul 2>&1
)

cd /d "%~dp0"

if "%MODE%"=="2" (
    echo   Starting server + public tunnel...
    echo   The public link will appear in a moment.
    echo.
    python server.py --public || py server.py --public
) else (
    python server.py || py server.py
)
pause
