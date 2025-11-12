@echo off
REM CRM Backend - Windows Uninstaller

echo =========================================
echo    CRM Backend - Windows Uninstaller
echo =========================================
echo.

set "APP_DIR=%USERPROFILE%\crm-backend"

if not exist "%APP_DIR%" (
    echo [!] CRM Backend is not installed at %APP_DIR%
    pause
    exit /b 1
)

echo [!] This will completely remove the CRM Backend installation
echo [!] Location: %APP_DIR%
echo.
set /p "CONFIRM=Are you sure? (yes/NO): "

if /i not "%CONFIRM%"=="yes" (
    echo [X] Uninstall cancelled
    pause
    exit /b 0
)

echo.
echo [i] Removing application directory...
rmdir /s /q "%APP_DIR%"
echo [V] Application removed

REM Remove desktop shortcut
set "SHORTCUT=%USERPROFILE%\Desktop\Start CRM Backend.bat"
if exist "%SHORTCUT%" (
    echo [i] Removing desktop shortcut...
    del /q "%SHORTCUT%"
    echo [V] Shortcut removed
)

echo.
echo =========================================
echo    Uninstall Complete!
echo =========================================
echo.
echo [V] CRM Backend has been removed from your system
echo.
pause
