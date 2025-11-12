@echo off
REM Create a portable package to build .deb on Ubuntu

setlocal enabledelayedexpansion

echo =========================================
echo    Creating Portable DEB Builder Package
echo =========================================
echo.

set TIMESTAMP=%date:~-4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set TIMESTAMP=%TIMESTAMP: =0%
set PACKAGE_NAME=crm-backend-deb-builder_%TIMESTAMP%

echo [i] Creating package directory...
mkdir "%PACKAGE_NAME%" 2>nul
mkdir "%PACKAGE_NAME%\app" 2>nul
mkdir "%PACKAGE_NAME%\alembic" 2>nul

echo [i] Copying files...
xcopy /E /I /Y app "%PACKAGE_NAME%\app" >nul
xcopy /E /I /Y alembic "%PACKAGE_NAME%\alembic" >nul
copy requirements.txt "%PACKAGE_NAME%\" >nul
copy alembic.ini "%PACKAGE_NAME%\" >nul
copy start_server.py "%PACKAGE_NAME%\" >nul
copy create_deb_package.sh "%PACKAGE_NAME%\" >nul
copy DEB_PACKAGE_GUIDE.md "%PACKAGE_NAME%\" >nul

echo [i] Creating README...
(
echo CRM Backend - DEB Package Builder
echo ==================================
echo.
echo This package contains everything needed to build a .deb package on Ubuntu/Debian.
echo.
echo Instructions:
echo -------------
echo 1. Transfer this entire folder to your Ubuntu machine
echo 2. Open terminal in this directory
echo 3. Run: chmod +x create_deb_package.sh
echo 4. Run: ./create_deb_package.sh
echo 5. Install: sudo dpkg -i crm-backend_1.2.0_all.deb
echo.
echo For detailed instructions, see DEB_PACKAGE_GUIDE.md
echo.
echo Package Contents:
echo -----------------
echo - app/                    Application code
echo - alembic/                Database migrations
echo - requirements.txt        Python dependencies
echo - create_deb_package.sh   DEB builder script
echo - DEB_PACKAGE_GUIDE.md    Complete documentation
echo.
) > "%PACKAGE_NAME%\README.txt"

echo [i] Creating build script...
(
echo @echo off
echo echo This script must be run on Ubuntu/Linux
echo echo.
echo echo Please transfer this folder to Ubuntu and run:
echo echo   chmod +x create_deb_package.sh
echo echo   ./create_deb_package.sh
echo pause
) > "%PACKAGE_NAME%\BUILD_ON_UBUNTU.bat"

echo [i] Creating zip archive...
powershell -Command "Compress-Archive -Path '%PACKAGE_NAME%' -DestinationPath '%PACKAGE_NAME%.zip' -Force"

if exist "%PACKAGE_NAME%.zip" (
    echo [i] Cleaning up...
    rmdir /S /Q "%PACKAGE_NAME%"
    
    echo.
    echo =========================================
    echo    Package Created Successfully!
    echo =========================================
    echo.
    
    for %%A in ("%PACKAGE_NAME%.zip") do echo Package: %PACKAGE_NAME%.zip
    for %%A in ("%PACKAGE_NAME%.zip") do echo Size: %%~zA bytes
    echo.
    echo Transfer to Ubuntu and extract, then run:
    echo   chmod +x create_deb_package.sh
    echo   ./create_deb_package.sh
    echo.
) else (
    echo [X] Failed to create package
    pause
)
