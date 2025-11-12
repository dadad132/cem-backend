@echo off
REM CRM Backend - Windows Package Creator

echo =========================================
echo    Creating Windows Installer Package
echo =========================================
echo.

set timestamp=%date:~-4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set timestamp=%timestamp: =0%
set "PACKAGE_NAME=crm-backend-windows-installer_%timestamp%"

echo [i] Creating package directory...
mkdir "%PACKAGE_NAME%" 2>nul

echo [i] Copying application files...
xcopy /E /I /Y /Q app "%PACKAGE_NAME%\app"
if exist "alembic" xcopy /E /I /Y /Q alembic "%PACKAGE_NAME%\alembic"

copy /Y requirements.txt "%PACKAGE_NAME%\" >nul
copy /Y alembic.ini "%PACKAGE_NAME%\" >nul
copy /Y .env.example "%PACKAGE_NAME%\" >nul
copy /Y start_server.py "%PACKAGE_NAME%\" >nul
copy /Y install_windows.bat "%PACKAGE_NAME%\" >nul
copy /Y install_windows.ps1 "%PACKAGE_NAME%\" >nul
copy /Y install_windows_complete.bat "%PACKAGE_NAME%\" >nul
copy /Y install_windows_complete.ps1 "%PACKAGE_NAME%\" >nul
copy /Y uninstall_windows.bat "%PACKAGE_NAME%\" >nul
copy /Y auto_update.bat "%PACKAGE_NAME%\" >nul
copy /Y README.md "%PACKAGE_NAME%\" >nul 2>nul
copy /Y INSTALL_WINDOWS.md "%PACKAGE_NAME%\" >nul 2>nul

echo [i] Creating directories...
mkdir "%PACKAGE_NAME%\logs" 2>nul
mkdir "%PACKAGE_NAME%\backups" 2>nul
mkdir "%PACKAGE_NAME%\updates" 2>nul
mkdir "%PACKAGE_NAME%\app\uploads\comments" 2>nul

echo [i] Creating installation instructions...
(
echo CRM BACKEND - WINDOWS INSTALLATION INSTRUCTIONS
echo ================================================
echo.
echo REQUIREMENTS:
echo - Windows 10/11
echo - Python 3.8 or higher
echo - Internet connection
echo.
echo INSTALLATION:
echo.
echo Method 1 - Complete Auto-Install ^(Recommended^):
echo   Right-click install_windows_complete.bat and Run as administrator
echo   OR Right-click install_windows_complete.ps1 and Run as administrator
echo   ^(Automatically installs Python if not found^)
echo.
echo Method 2 - If You Already Have Python:
echo   Double-click install_windows.bat
echo   OR Right-click install_windows.ps1 and Run with PowerShell
echo.
echo AFTER INSTALLATION:
echo - Desktop shortcut: Start CRM Backend
echo - Access at: http://localhost:8000
echo.
echo For more information, see README.md
) > "%PACKAGE_NAME%\INSTALL_WINDOWS.txt"

echo [i] Creating zip archive...
powershell -Command "Compress-Archive -Path '%PACKAGE_NAME%' -DestinationPath '%PACKAGE_NAME%.zip' -Force"

echo [i] Cleaning up...
rmdir /s /q "%PACKAGE_NAME%"

echo.
echo =========================================
echo    Package Created Successfully!
echo =========================================
echo.
echo Package: %PACKAGE_NAME%.zip
for %%A in ("%PACKAGE_NAME%.zip") do echo Size: %%~zA bytes
echo.
echo To install on Windows:
echo 1. Extract the zip file
echo 2. Run install_windows.bat or install_windows.ps1
echo 3. Follow the instructions
echo.
