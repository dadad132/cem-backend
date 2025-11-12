@echo off
REM CRM Backend - Package Creator for Windows
REM Creates a deployable package for Ubuntu installation

echo =========================================
echo    Creating Ubuntu Installer Package
echo =========================================
echo.

REM Get timestamp - use simpler method
set timestamp=%date:~-4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set timestamp=%timestamp: =0%

REM Package name
set "PACKAGE_NAME=crm-backend-installer"

REM Create package directory
echo [i] Creating package directory...
mkdir "%PACKAGE_NAME%" 2>nul

REM Copy files and directories
echo [i] Copying application files...
xcopy /E /I /Y app "%PACKAGE_NAME%\app" >nul
xcopy /E /I /Y alembic "%PACKAGE_NAME%\alembic" >nul
copy /Y requirements.txt "%PACKAGE_NAME%\" >nul
copy /Y alembic.ini "%PACKAGE_NAME%\" >nul
copy /Y .env.example "%PACKAGE_NAME%\" >nul
copy /Y install_ubuntu.sh "%PACKAGE_NAME%\" >nul
copy /Y install_ubuntu_debug.sh "%PACKAGE_NAME%\" >nul
copy /Y uninstall_ubuntu.sh "%PACKAGE_NAME%\" >nul
copy /Y update_ubuntu.sh "%PACKAGE_NAME%\" >nul
copy /Y auto_update.sh "%PACKAGE_NAME%\" >nul
copy /Y auto_update.bat "%PACKAGE_NAME%\" >nul
copy /Y INSTALLER_README.md "%PACKAGE_NAME%\" >nul
copy /Y README.md "%PACKAGE_NAME%\" >nul 2>nul

REM Create empty directories
echo [i] Creating directories...
mkdir "%PACKAGE_NAME%\logs" 2>nul
mkdir "%PACKAGE_NAME%\backups" 2>nul
mkdir "%PACKAGE_NAME%\updates" 2>nul
mkdir "%PACKAGE_NAME%\app\uploads\comments" 2>nul
echo .gitkeep > "%PACKAGE_NAME%\logs\.gitkeep"
echo .gitkeep > "%PACKAGE_NAME%\backups\.gitkeep"

REM Create installation instructions
echo [i] Creating installation instructions...
(
echo CRM BACKEND - INSTALLATION INSTRUCTIONS
echo ========================================
echo.
echo 1. Transfer this entire folder to your Ubuntu server
echo.
echo 2. Make the installer executable:
echo    chmod +x install_ubuntu.sh
echo.
echo 3. Run the installer:
echo    ./install_ubuntu.sh
echo.
echo 4. Follow the on-screen instructions
echo.
echo 5. Access your application at:
echo    http://YOUR-SERVER-IP:8000
echo.
echo For detailed instructions, see INSTALLER_README.md
echo.
echo Support: Check logs with 'sudo journalctl -u crm-backend -f'
) > "%PACKAGE_NAME%\INSTALL.txt"

REM Create a zip file if 7-Zip is available
if exist "C:\Program Files\7-Zip\7z.exe" (
    echo [i] Creating zip archive...
    "C:\Program Files\7-Zip\7z.exe" a -tzip "%PACKAGE_NAME%.zip" "%PACKAGE_NAME%" >nul
    echo [✓] Created %PACKAGE_NAME%.zip
) else if exist "C:\Program Files (x86)\7-Zip\7z.exe" (
    echo [i] Creating zip archive...
    "C:\Program Files (x86)\7-Zip\7z.exe" a -tzip "%PACKAGE_NAME%.zip" "%PACKAGE_NAME%" >nul
    echo [✓] Created %PACKAGE_NAME%.zip
) else (
    echo [!] 7-Zip not found. Package folder created but not compressed.
    echo [!] You can manually zip the folder: %PACKAGE_NAME%
)

echo.
echo =========================================
echo    Package Created Successfully!
echo =========================================
echo.
echo Package folder: %PACKAGE_NAME%
if exist "%PACKAGE_NAME%.zip" (
    echo Package file: %PACKAGE_NAME%.zip
)
echo.
echo To deploy to Ubuntu:
echo   1. Transfer the folder/zip to your Ubuntu server
echo   2. Extract if zipped: unzip %PACKAGE_NAME%.zip
echo   3. cd %PACKAGE_NAME%
echo   4. chmod +x install_ubuntu.sh
echo   5. ./install_ubuntu.sh
echo.
echo Press any key to exit...
pause >nul
