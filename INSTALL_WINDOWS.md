# CRM Backend - Windows Installation Guide

## Quick Start

### 1. Extract this ZIP file
Extract all files to a folder on your computer (e.g., Desktop, Downloads, etc.)

### 2. Choose Installation Method

#### Option A: Complete Auto-Install (Recommended)
**Best for users who DON'T have Python installed**

1. Open the extracted folder
2. Right-click `install_windows_complete.bat`
3. Select "Run as administrator"
4. Wait for Python to download and install
5. The CRM Backend will install automatically

OR use PowerShell:
1. Right-click `install_windows_complete.ps1`
2. Select "Run as administrator"

#### Option B: Standard Install
**For users who ALREADY have Python 3.8+ installed**

1. Open the extracted folder
2. Double-click `install_windows.bat`
3. Follow the prompts

OR use PowerShell:
1. Right-click `install_windows.ps1`
2. Select "Run with PowerShell"

### 3. After Installation

- **Location**: The application will be installed to `C:\Users\YourName\crm-backend`
- **Desktop Shortcut**: A shortcut named "Start CRM Backend" will be created on your desktop
- **Access**: Open your browser and go to `http://localhost:8000`

## Starting the Server

### Method 1: Desktop Shortcut
Double-click "Start CRM Backend" on your desktop

### Method 2: Manual Start
1. Open Command Prompt or PowerShell
2. Navigate to: `cd %USERPROFILE%\crm-backend`
3. Activate environment: `.venv\Scripts\activate`
4. Start server: `python start_server.py`

## Troubleshooting

### "Installation files not found"
- **Problem**: You're running the installer from the wrong location
- **Solution**: Make sure you're inside the extracted folder that contains `app/`, `requirements.txt`, etc.

### "Python not found"
- **Solution 1**: Use `install_windows_complete.bat` (run as administrator)
- **Solution 2**: Install Python manually from https://www.python.org/downloads/
  - Download Python 3.11 or higher
  - During installation, check "Add Python to PATH"
  - Restart computer after installation
  - Run `install_windows.bat`

### "Access Denied" or "Permission Error"
- **Solution**: Right-click the installer and select "Run as administrator"

### Server Won't Start
1. Check if Python is installed: `python --version`
2. Check the installation directory: `%USERPROFILE%\crm-backend`
3. Try running manually: `cd %USERPROFILE%\crm-backend` then `python start_server.py`

## Uninstalling

1. Navigate to `%USERPROFILE%\crm-backend`
2. Run `uninstall_windows.bat`

OR manually:
1. Delete folder: `C:\Users\YourName\crm-backend`
2. Delete desktop shortcut: "Start CRM Backend"

## System Requirements

- **OS**: Windows 10/11 (64-bit)
- **RAM**: 2GB minimum, 4GB recommended
- **Disk Space**: 500MB for application + 100MB for Python (if not installed)
- **Internet**: Required for installation and updates

## What Gets Installed

### With Complete Installer:
- Python 3.11.9 (~30MB) - if not already installed
- CRM Backend application
- All Python dependencies
- SQLite database
- Desktop shortcut

### Installation Location:
```
C:\Users\YourName\crm-backend\
├── app\              (Application code)
├── .venv\            (Python virtual environment)
├── logs\             (Application logs)
├── backups\          (Database backups)
├── data.db           (SQLite database)
├── .env              (Configuration file)
└── start_server.py   (Server startup script)
```

## Configuration

Edit the `.env` file in the installation directory to configure:
- Database settings
- Server port (default: 8000)
- Email settings
- Update check settings

## Support

For issues or questions:
1. Check this README
2. Check the logs: `%USERPROFILE%\crm-backend\logs\`
3. Refer to the main README.md

## Version

**Version**: 1.2.0
**Build Date**: January 15, 2025

---

**Need Help?**
Make sure you:
1. Extract ALL files from the ZIP
2. Run the installer FROM the extracted folder
3. Use "Run as administrator" for complete install
4. Have internet connection during installation
