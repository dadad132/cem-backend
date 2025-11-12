@echo off
REM CRM Backend - Windows Installer
REM Automatically sets up the CRM backend on Windows

setlocal enabledelayedexpansion

REM Get the directory where this script is located
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
echo [i] Running from: %SCRIPT_DIR%
echo.

echo =========================================
echo    CRM Backend - Windows Installer
echo =========================================
echo.

REM Check if running as administrator
net session >nul 2>&1
if %errorLevel% == 0 (
    echo [!] Please do NOT run this script as Administrator
    echo Run as your regular user: install_windows.bat
    pause
    exit /b 1
)

echo [i] Starting installation process...
echo [i] Current user: %USERNAME%
echo [i] Current directory: %CD%
echo.

REM Check if we're in the correct directory
if not exist "app" (
    echo [X] Error: Installation files not found!
    echo.
    echo [!] Please run this script from the extracted installer folder
    echo [!] The folder should contain: app/, requirements.txt, start_server.py, etc.
    echo.
    pause
    exit /b 1
)

REM Check if Python is installed
echo [i] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python not found!
    echo.
    echo Please install Python 3.8 or higher from:
    echo https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add Python to PATH" during installation
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo [V] Python found: %PYTHON_VERSION%
echo.

REM Check if Git is installed (optional but recommended)
git --version >nul 2>&1
if errorlevel 1 (
    echo [!] Git not found - Updates will need to be done manually
) else (
    for /f "tokens=3" %%i in ('git --version 2^>^&1') do set GIT_VERSION=%%i
    echo [V] Git found: !GIT_VERSION!
)
echo.

REM Create application directory
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

REM Check if requirements.txt exists
if not exist "requirements.txt" (
    echo [X] requirements.txt not found!
    pause
    exit /b 1
)

REM Create virtual environment
echo [i] Creating Python virtual environment...
python -m venv .venv
if errorlevel 1 (
    echo [X] Failed to create virtual environment
    pause
    exit /b 1
)
echo [V] Virtual environment created
echo.

REM Activate virtual environment
echo [i] Activating virtual environment...
call .venv\Scripts\activate.bat
echo [V] Virtual environment activated
echo [i] Python location: %VIRTUAL_ENV%\Scripts\python.exe
echo.

REM Upgrade pip
echo [i] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo [V] Pip upgraded
echo.

REM Install Python dependencies
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

REM Create necessary directories
echo [i] Creating necessary directories...
if not exist "logs" mkdir logs
if not exist "backups" mkdir backups
if not exist "updates" mkdir updates
if not exist "app\uploads\comments" mkdir app\uploads\comments
echo [V] Directories created
echo.

REM Create .env file
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

REM Initialize database
echo [i] Initializing database...
python -c "import asyncio; import sys; sys.path.insert(0, '.'); from app.core.database import init_models; asyncio.run(init_models()); print('Database initialized successfully')" 2>nul
if errorlevel 1 (
    echo [!] Database may already be initialized
) else (
    echo [V] Database initialized
)
echo.

REM Create startup shortcut on desktop
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

REM Get local IP
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
echo     3. Or use: start_server.bat
echo.
echo [*] Useful Commands:
echo     Start server:    python start_server.py
echo     Update:          auto_update.bat
echo     Location:        cd %APP_DIR%
echo.
echo [V] You can now start the CRM backend!
echo.
pause
