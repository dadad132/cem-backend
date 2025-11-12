@echo off
REM CRM Backend Deployment Package Creator for Windows
REM This script creates a deployment package that can be extracted on Ubuntu

setlocal enabledelayedexpansion

echo ============================================================
echo Creating CRM Backend Deployment Package
echo ============================================================
echo.

REM Check if 7-Zip is available
where 7z >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: 7-Zip is not installed or not in PATH
    echo Please install 7-Zip from https://www.7-zip.org/
    echo Or use WSL/Linux to run create_package.sh
    pause
    exit /b 1
)

REM Configuration
set PACKAGE_NAME=crm-backend-deploy
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
set VERSION=%datetime:~0,8%_%datetime:~8,6%
set PACKAGE_FILE=%PACKAGE_NAME%_%VERSION%.tar.gz

echo Package configuration:
echo    Name: %PACKAGE_NAME%
echo    Version: %VERSION%
echo    Output: %PACKAGE_FILE%
echo.

REM Get current directory
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..

REM Create temporary directory
set TEMP_DIR=%TEMP%\crm_package_%RANDOM%
mkdir "%TEMP_DIR%"
mkdir "%TEMP_DIR%\%PACKAGE_NAME%"

echo Creating package structure...

REM Copy application files (excluding unwanted directories)
echo Copying application files...
cd "%PROJECT_ROOT%"

xcopy /E /I /Y /Q "app" "%TEMP_DIR%\%PACKAGE_NAME%\app\" >nul
xcopy /E /I /Y /Q "deployment" "%TEMP_DIR%\%PACKAGE_NAME%\deployment\" >nul
copy /Y "start_server.py" "%TEMP_DIR%\%PACKAGE_NAME%\" >nul
copy /Y "requirements.txt" "%TEMP_DIR%\%PACKAGE_NAME%\" >nul
copy /Y "*.md" "%TEMP_DIR%\%PACKAGE_NAME%\" 2>nul

echo Files copied

REM Create installation README
(
echo ============================================================
echo CRM Backend - Installation Instructions
echo ============================================================
echo.
echo SYSTEM REQUIREMENTS:
echo   - Ubuntu 20.04 LTS or newer
echo   - 2GB RAM minimum
echo   - 10GB disk space
echo   - Python 3.8 or newer
echo   - Root/sudo access
echo.
echo INSTALLATION STEPS:
echo.
echo 1. Extract this package:
echo    tar -xzf crm-backend-deploy_*.tar.gz
echo    cd crm-backend-deploy
echo.
echo 2. Run the installation script:
echo    sudo bash deployment/install.sh
echo.
echo 3. Start the application:
echo    sudo crm-start
echo.
echo 4. Access the application:
echo    Open your browser to: http://YOUR_SERVER_IP:8000
echo.
echo 5. Create your first admin user through the web interface
echo.
echo MANAGEMENT COMMANDS:
echo   crm-start    - Start the application
echo   crm-stop     - Stop the application ^(graceful, saves data^)
echo   crm-restart  - Restart the application
echo   crm-status   - Check application status
echo   crm-logs     - View live application logs
echo   crm-backup   - Create manual database backup
echo.
echo IMPORTANT NOTES:
echo   - Each server starts fresh with NO pre-existing users
echo   - First user created through signup becomes workspace admin
echo   - Database is automatically backed up every 12 hours
echo   - Backups are stored in /opt/crm-backend/backups
echo   - Use 'crm-stop' for safe shutdown ^(preserves all data^)
echo.
echo TROUBLESHOOTING:
echo   - Check status: sudo crm-status
echo   - View logs: sudo crm-logs
echo   - Check service: sudo systemctl status crm-backend
echo   - Restart: sudo crm-restart
echo.
echo For support, contact your system administrator.
echo ============================================================
) > "%TEMP_DIR%\%PACKAGE_NAME%\INSTALL_README.txt"

REM Create version info
(
echo CRM Backend Deployment Package
echo Version: %VERSION%
echo Build Date: %date% %time%
echo Package: %PACKAGE_FILE%
) > "%TEMP_DIR%\%PACKAGE_NAME%\VERSION.txt"

echo Documentation created

REM Create the package directory
mkdir "%PROJECT_ROOT%\deployment\packages" 2>nul

REM Create tar.gz package using 7-Zip
echo Creating deployment package...
cd "%TEMP_DIR%"

REM First create tar, then gzip
7z a -ttar "%PACKAGE_NAME%.tar" "%PACKAGE_NAME%" -r >nul
7z a -tgzip "%PROJECT_ROOT%\deployment\packages\%PACKAGE_FILE%" "%PACKAGE_NAME%.tar" >nul

REM Cleanup
cd "%PROJECT_ROOT%"
rmdir /S /Q "%TEMP_DIR%"

REM Get package size
for %%A in ("%PROJECT_ROOT%\deployment\packages\%PACKAGE_FILE%") do set PACKAGE_SIZE=%%~zA
set /A PACKAGE_SIZE_MB=!PACKAGE_SIZE! / 1048576

echo.
echo ============================================================
echo Deployment Package Created Successfully!
echo ============================================================
echo.
echo Package Details:
echo    File: %PACKAGE_FILE%
echo    Location: deployment\packages\%PACKAGE_FILE%
echo    Size: !PACKAGE_SIZE_MB! MB
echo.
echo Deployment Instructions:
echo.
echo 1. Copy package to your Ubuntu server:
echo    scp deployment/packages/%PACKAGE_FILE% user@server:/tmp/
echo.
echo 2. On the server, extract and install:
echo    cd /tmp
echo    tar -xzf %PACKAGE_FILE%
echo    cd %PACKAGE_NAME%
echo    sudo bash deployment/install.sh
echo.
echo 3. Start the application:
echo    sudo crm-start
echo.
echo 4. Access at: http://YOUR_SERVER_IP:8000
echo.
echo ============================================================
echo.

pause
