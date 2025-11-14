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
FOR /F "tokens=*" %%a IN ('python -c "from app.core.version import VERSION; print(VERSION)" 2^>nul') DO SET CURRENT_VERSION=%%a
if "%CURRENT_VERSION%"=="" (
    FOR /F "tokens=*" %%a IN ('python3 -c "from app.core.version import VERSION; print(VERSION)"') DO SET CURRENT_VERSION=%%a
)
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
echo    - Upgrading pip...
python -m pip install --upgrade pip -q 2>nul || python3 -m pip install --upgrade pip -q
echo    - Installing/upgrading all requirements...
python -m pip install -r requirements.txt --upgrade -q 2>nul || python3 -m pip install -r requirements.txt --upgrade -q
echo [*] Dependencies updated

REM Run database migrations
echo [i] Running database migrations...
python -c "import asyncio; from app.core.database import init_models; asyncio.run(init_models())" 2>nul || python3 -c "import asyncio; from app.core.database import init_models; asyncio.run(init_models())" 2>nul
echo [*] Base database migrations complete

REM Run migration scripts
echo [i] Running migration scripts...
if exist migrate_google_oauth.py python migrate_google_oauth.py 2>nul || python3 migrate_google_oauth.py 2>nul
if exist migrate_subtasks.py python migrate_subtasks.py 2>nul || python3 migrate_subtasks.py 2>nul
if exist migrate_notifications_interactive.py python migrate_notifications_interactive.py 2>nul || python3 migrate_notifications_interactive.py 2>nul
if exist migrate_project_archive.py python migrate_project_archive.py 2>nul || python3 migrate_project_archive.py 2>nul
if exist migrate_ticket_system.py python migrate_ticket_system.py 2>nul || python3 migrate_ticket_system.py 2>nul
if exist migrate_guest_tickets_combined.py python migrate_guest_tickets_combined.py 2>nul || python3 migrate_guest_tickets_combined.py 2>nul
if exist migrate_processed_mail.py python migrate_processed_mail.py 2>nul || python3 migrate_processed_mail.py 2>nul
if exist migrate_calendar_features.py python migrate_calendar_features.py 2>nul || python3 migrate_calendar_features.py 2>nul
echo [*] Migration scripts complete

REM Get new version
FOR /F "tokens=*" %%a IN ('python -c "from app.core.version import VERSION; print(VERSION)" 2^>nul') DO SET NEW_VERSION=%%a
if "%NEW_VERSION%"=="" (
    FOR /F "tokens=*" %%a IN ('python3 -c "from app.core.version import VERSION; print(VERSION)"') DO SET NEW_VERSION=%%a
)

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
