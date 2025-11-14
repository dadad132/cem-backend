@echo off
REM CRM Backend - Complete Windows Installer with Python Auto-Install
REM This script will install Python if not found, then install the CRM Backend

setlocal enabledelayedexpansion

echo =========================================
echo    CRM Backend - Complete Installer
echo =========================================
echo.

REM Get the directory where this script is located
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
echo [i] Running from: %SCRIPT_DIR%
echo.

REM Check for administrator privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    set "IS_ADMIN=1"
    echo [i] Running with Administrator privileges
) else (
    set "IS_ADMIN=0"
    echo [i] Running as regular user
)
echo.

echo [i] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python not found!
    echo.
    
    if "%IS_ADMIN%"=="0" (
        echo [!] Python installation requires Administrator privileges
        echo.
        echo Please run this installer as Administrator:
        echo 1. Right-click this file
        echo 2. Select "Run as administrator"
        echo.
        echo Or install Python manually from:
        echo https://www.python.org/downloads/
        pause
        exit /b 1
    )
    
    echo [i] Downloading Python installer...
    echo [i] Please wait, this may take a few minutes...
    
    REM Download Python 3.11 installer (stable and compatible)
    set "PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    set "PYTHON_INSTALLER=%TEMP%\python_installer.exe"
    
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_INSTALLER%'}"
    
    if errorlevel 1 (
        echo [X] Failed to download Python installer
        echo.
        echo Please install Python manually from:
        echo https://www.python.org/downloads/
        pause
        exit /b 1
    )
    
    echo [V] Python installer downloaded
    echo.
    echo [i] Installing Python...
    echo [i] This will take a few minutes. Please wait...
    
    REM Install Python silently with pip and add to PATH
    "%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0
    
    if errorlevel 1 (
        echo [X] Python installation failed
        pause
        exit /b 1
    )
    
    echo [V] Python installed successfully!
    echo.
    
    REM Clean up installer
    del "%PYTHON_INSTALLER%" >nul 2>&1
    
    REM Refresh environment variables
    echo [i] Refreshing environment...
    call :RefreshEnv
    
    echo [V] Python installation complete
    echo.
    
    REM Verify Python is now available
    python --version >nul 2>&1
    if errorlevel 1 (
        echo [!] Python installed but not found in PATH
        echo [!] Please close this window and run the installer again
        pause
        exit /b 1
    )
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo [V] Python found: %PYTHON_VERSION%
echo.

REM Check if Git is installed (optional)
git --version >nul 2>&1
if errorlevel 1 (
    echo [!] Git not found - Updates will need to be done manually
) else (
    for /f "tokens=3" %%i in ('git --version 2^>^&1') do set GIT_VERSION=%%i
    echo [V] Git found: !GIT_VERSION!
)
echo.

REM Now proceed with CRM Backend installation
echo [i] Starting CRM Backend installation...
echo.

REM Check if we're in the correct directory
if not exist "app" (
    echo [X] Error: Installation files not found!
    echo.
    echo [!] Please make sure you are running this script from the extracted installer folder
    echo [!] The folder should contain: app/, requirements.txt, start_server.py, etc.
    echo.
    echo Current directory: %CD%
    echo.
    pause
    exit /b 1
)

set "APP_DIR=%USERPROFILE%\crm-backend"

if exist "%APP_DIR%" (
    echo [!] Application directory already exists at %APP_DIR%
    set /p "REINSTALL=Remove and reinstall? (y/N): "
    if /i "!REINSTALL!"=="y" (
        echo [i] Removing existing installation...
        rmdir /s /q "%APP_DIR%"
        echo [V] Existing installation removed
    ) else (
        echo [X] Installation cancelled
        pause
        exit /b 1
    )
)

echo [i] Creating application directory: %APP_DIR%
mkdir "%APP_DIR%"

REM Copy files to application directory
echo [i] Copying application files...
xcopy /E /I /Y /Q app "%APP_DIR%\app"
if exist "alembic" xcopy /E /I /Y /Q alembic "%APP_DIR%\alembic"
if exist "requirements.txt" copy /Y requirements.txt "%APP_DIR%\"
if exist "alembic.ini" copy /Y alembic.ini "%APP_DIR%\"
if exist ".env.example" copy /Y .env.example "%APP_DIR%\"
if exist "start_server.py" copy /Y start_server.py "%APP_DIR%\"
if exist "auto_update.bat" copy /Y auto_update.bat "%APP_DIR%\"
if exist "README.md" copy /Y README.md "%APP_DIR%\"
echo [V] Files copied successfully
echo.

cd /d "%APP_DIR%"
echo [V] Changed to directory: %CD%
echo.

if not exist "requirements.txt" (
    echo [X] requirements.txt not found!
    pause
    exit /b 1
)

echo [i] Creating Python virtual environment...
python -m venv .venv
if errorlevel 1 (
    echo [X] Failed to create virtual environment
    pause
    exit /b 1
)
echo [V] Virtual environment created
echo.

echo [i] Activating virtual environment...
call .venv\Scripts\activate.bat
echo [V] Virtual environment activated
echo.

echo [i] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo [V] Pip upgraded
echo.

echo [i] Installing Python dependencies (this may take a few minutes)...
echo [i] Please wait...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [X] Failed to install Python dependencies
    echo [!] Try running: pip install -r requirements.txt
    pause
    exit /b 1
)
echo [V] Python dependencies installed successfully!
echo.

echo [i] Creating necessary directories...
if not exist "logs" mkdir logs
if not exist "backups" mkdir backups
if not exist "updates" mkdir updates
if not exist "app\uploads\comments" mkdir app\uploads\comments
echo [V] Directories created
echo.

if not exist ".env" (
    echo [i] Creating .env configuration file...
    (
        echo # CRM Backend Configuration
        echo DATABASE_URL=sqlite+aiosqlite:///./data.db
        echo SECRET_KEY=change-this-to-a-random-secret-key-in-production
        echo ALGORITHM=HS256
        echo ACCESS_TOKEN_EXPIRE_MINUTES=30
        echo.
        echo # Server Configuration
        echo HOST=0.0.0.0
        echo PORT=8000
        echo.
        echo # Update System Configuration
        echo UPDATE_CHECK_ENABLED=true
        echo UPDATE_CHECK_URL=https://api.github.com/repos/yourusername/crm-backend/releases/latest
        echo UPDATE_CHECK_INTERVAL=86400
    ) > .env
    echo [V] .env file created
) else (
    echo [i] .env file already exists
)
echo.

echo [i] Initializing database...
python -c "import asyncio; import sys; sys.path.insert(0, '.'); from app.core.database import init_models; asyncio.run(init_models()); print('Database initialized successfully')" 2>nul
if errorlevel 1 (
    echo [!] Database may already be initialized
) else (
    echo [V] Database initialized
)
echo.

echo [i] Creating desktop shortcut...
set "SHORTCUT=%USERPROFILE%\Desktop\Start CRM Backend.bat"
(
    echo @echo off
    echo cd /d "%APP_DIR%"
    echo call .venv\Scripts\activate.bat
    echo python start_server.py
    echo pause
) > "%SHORTCUT%"
echo [V] Desktop shortcut created
echo.

for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    set "LOCAL_IP=%%a"
    goto :found_ip
)
:found_ip
set LOCAL_IP=%LOCAL_IP:~1%

echo.
echo =========================================
echo    Installation Complete!
echo =========================================
echo.
echo [*] Access URLs:
echo     Local:    http://localhost:8000
echo     Network:  http://%LOCAL_IP%:8000
echo.
echo [*] Application Directory:
echo     Location: %APP_DIR%
echo     Config:   %APP_DIR%\.env
echo     Database: %APP_DIR%\data.db
echo.
echo [*] How to Start the Server:
echo     1. Double-click "Start CRM Backend" on your desktop
echo     2. Or run: python start_server.py
echo.
echo [V] Installation complete! You can now start the CRM backend.
echo.
pause
exit /b 0

REM Function to refresh environment variables
:RefreshEnv
REM Refresh PATH from registry
for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "UserPath=%%b"
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul') do set "SystemPath=%%b"
set "PATH=%UserPath%;%SystemPath%"
exit /b 0
