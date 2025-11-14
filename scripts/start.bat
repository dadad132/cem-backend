@echo off
REM Windows batch file to start the server with local IP detection

echo Starting CRM Backend Server...
echo.

python start_server.py %*

pause
