@echo off
REM CRM Backend - Windows Update Script
REM Pulls latest code and restarts the server

echo =========================================
echo    CRM Backend - Auto Update
echo =========================================
echo.

cd /d "%~dp0"

echo [i] Checking for updates...

REM Get current version
FOR /F "tokens=*" %%a IN ('python3 -c "from app.core.version import VERSION; print(VERSION)"') DO SET CURRENT_VERSION=%%a
echo [*] Current version: %CURRENT_VERSION%

REM Create backup
echo [i] Creating backup...
set BACKUP_FILE=data_backup_update_%date:~-4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%.db
set BACKUP_FILE=%BACKUP_FILE: =0%
if exist data.db (
    copy data.db backups\%BACKUP_FILE% >nul
    echo [*] Database backed up to backups\%BACKUP_FILE%
)

REM Pull latest code (if using git)
if exist .git (
    echo [i] Pulling latest code from repository...
    git pull origin main 2>nul || git pull origin master 2>nul
    echo [*] Code updated
) else (
    echo [!] Not a git repository. Manual update required.
)

REM Update dependencies
echo [i] Updating dependencies...
python3 -m pip install --upgrade pip -q
python3 -m pip install -r requirements.txt --upgrade -q
echo [*] Dependencies updated

REM Run database migrations
echo [i] Checking database migrations...
python3 -c "import asyncio; from app.core.database import init_models; asyncio.run(init_models())" 2>nul
echo [*] Database migrations complete

REM Get new version
FOR /F "tokens=*" %%a IN ('python3 -c "from app.core.version import VERSION; print(VERSION)"') DO SET NEW_VERSION=%%a

echo.
echo =========================================
echo    Update Complete!
echo =========================================
echo.
echo Previous version: %CURRENT_VERSION%
echo Current version:  %NEW_VERSION%
echo.
echo Please restart the server manually:
echo   python3 start_server.py
echo.
pause
