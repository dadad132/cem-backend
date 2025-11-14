#!/bin/bash

###############################################################################
# CRM Backend - Ubuntu Installer (VERBOSE DEBUG VERSION)
# This version shows detailed output and doesn't exit on errors
###############################################################################

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
APP_NAME="crm-backend"
APP_DIR="$HOME/$APP_NAME"
SERVICE_NAME="crm-backend"
PORT=8000
LOG_FILE="$HOME/crm_install_log.txt"

# Function to log and print
log_and_print() {
    echo -e "$1" | tee -a "$LOG_FILE"
}

# Start logging
echo "" > "$LOG_FILE"
log_and_print "${BLUE}=========================================${NC}"
log_and_print "${BLUE}   CRM Backend - Ubuntu Installer${NC}"
log_and_print "${BLUE}   (VERBOSE DEBUG VERSION)${NC}"
log_and_print "${BLUE}=========================================${NC}\n"
log_and_print "Installation log: $LOG_FILE"
log_and_print ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
    log_and_print "${RED}[✗]${NC} Please do not run this script as root or with sudo"
    log_and_print "Run as your regular user: ./install_ubuntu_debug.sh"
    read -p "Press Enter to exit..."
    exit 1
fi

log_and_print "${YELLOW}[i]${NC} Starting installation process..."
log_and_print "${YELLOW}[i]${NC} Current user: $USER"
log_and_print "${YELLOW}[i]${NC} Home directory: $HOME"
log_and_print "${YELLOW}[i]${NC} Script directory: $(pwd)"
log_and_print ""

# Check internet connectivity
log_and_print "${YELLOW}[i]${NC} Testing internet connectivity..."
if ping -c 1 8.8.8.8 &> /dev/null; then
    log_and_print "${GREEN}[✓]${NC} Internet connection: OK"
else
    log_and_print "${RED}[✗]${NC} No internet connection detected!"
    log_and_print "${YELLOW}[!]${NC} Installation may fail. Please check your network."
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check Python
log_and_print "${YELLOW}[i]${NC} Checking Python installation..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    log_and_print "${GREEN}[✓]${NC} Python found: $PYTHON_VERSION"
else
    log_and_print "${RED}[✗]${NC} Python3 not found!"
    log_and_print "${YELLOW}[i]${NC} Installing Python3..."
    sudo apt update
    sudo apt install -y python3 python3-pip python3-venv
fi

# Update system packages
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Updating system packages..."
if sudo apt update 2>&1 | tee -a "$LOG_FILE"; then
    log_and_print "${GREEN}[✓]${NC} System packages updated"
else
    log_and_print "${RED}[✗]${NC} Failed to update packages (continuing anyway)"
fi

# Install dependencies
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Installing system dependencies..."
if sudo apt install -y python3 python3-pip python3-venv python3-dev build-essential git curl sqlite3 2>&1 | tee -a "$LOG_FILE"; then
    log_and_print "${GREEN}[✓]${NC} Dependencies installed"
else
    log_and_print "${RED}[✗]${NC} Some dependencies may have failed to install"
fi

# Check if application directory exists
log_and_print ""
if [ -d "$APP_DIR" ]; then
    log_and_print "${YELLOW}[!]${NC} Application directory already exists at $APP_DIR"
    read -p "Remove and reinstall? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_and_print "${YELLOW}[i]${NC} Removing existing installation..."
        rm -rf "$APP_DIR"
        log_and_print "${GREEN}[✓]${NC} Existing installation removed"
    else
        log_and_print "${RED}[✗]${NC} Installation cancelled"
        read -p "Press Enter to exit..."
        exit 1
    fi
fi

# Create application directory
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Creating application directory: $APP_DIR"
mkdir -p "$APP_DIR"

# Copy files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$SCRIPT_DIR" != "$APP_DIR" ]; then
    log_and_print "${YELLOW}[i]${NC} Copying application files..."
    log_and_print "${YELLOW}[i]${NC} From: $SCRIPT_DIR"
    log_and_print "${YELLOW}[i]${NC} To: $APP_DIR"
    
    if cp -rv "$SCRIPT_DIR"/* "$APP_DIR/" 2>&1 | tee -a "$LOG_FILE"; then
        log_and_print "${GREEN}[✓]${NC} Files copied successfully"
    else
        log_and_print "${RED}[✗]${NC} Failed to copy files"
        read -p "Press Enter to exit..."
        exit 1
    fi
fi

cd "$APP_DIR"
log_and_print "${GREEN}[✓]${NC} Changed to directory: $(pwd)"

# Check if requirements.txt exists
log_and_print ""
if [ ! -f "requirements.txt" ]; then
    log_and_print "${RED}[✗]${NC} requirements.txt not found in $APP_DIR!"
    log_and_print "${YELLOW}[i]${NC} Directory contents:"
    ls -la | tee -a "$LOG_FILE"
    read -p "Press Enter to exit..."
    exit 1
else
    log_and_print "${GREEN}[✓]${NC} Found requirements.txt"
fi

# Create virtual environment
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Creating Python virtual environment..."
if python3 -m venv .venv 2>&1 | tee -a "$LOG_FILE"; then
    log_and_print "${GREEN}[✓]${NC} Virtual environment created"
else
    log_and_print "${RED}[✗]${NC} Failed to create virtual environment"
    read -p "Press Enter to exit..."
    exit 1
fi

# Activate virtual environment
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Activating virtual environment..."
source .venv/bin/activate
log_and_print "${GREEN}[✓]${NC} Virtual environment activated"
log_and_print "${YELLOW}[i]${NC} Python location: $(which python3)"

# Upgrade pip
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Upgrading pip..."
if pip install --upgrade pip 2>&1 | tee -a "$LOG_FILE"; then
    log_and_print "${GREEN}[✓]${NC} Pip upgraded"
else
    log_and_print "${RED}[✗]${NC} Failed to upgrade pip (continuing anyway)"
fi

# Install Python dependencies
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Installing Python dependencies (this may take a few minutes)..."
log_and_print "${YELLOW}[i]${NC} Reading requirements.txt:"
cat requirements.txt | tee -a "$LOG_FILE"
log_and_print ""

if pip install -r requirements.txt 2>&1 | tee -a "$LOG_FILE"; then
    log_and_print "${GREEN}[✓]${NC} Python dependencies installed successfully!"
else
    log_and_print "${RED}[✗]${NC} Failed to install Python dependencies"
    log_and_print "${YELLOW}[!]${NC} Check the log above for details"
    read -p "Press Enter to exit..."
    exit 1
fi

# Create directories
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Creating necessary directories..."
mkdir -p logs backups app/uploads/comments updates
log_and_print "${GREEN}[✓]${NC} Directories created"

# Create .env file
log_and_print ""
if [ ! -f ".env" ]; then
    log_and_print "${YELLOW}[i]${NC} Creating .env configuration file..."
    cat > .env << 'EOF'
DATABASE_URL=sqlite+aiosqlite:///./data.db
SECRET_KEY=change-this-to-a-random-secret-key-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
HOST=0.0.0.0
PORT=8000
UPDATE_CHECK_ENABLED=true
UPDATE_CHECK_URL=https://api.github.com/repos/yourusername/crm-backend/releases/latest
UPDATE_CHECK_INTERVAL=86400
EOF
    log_and_print "${GREEN}[✓]${NC} .env file created"
else
    log_and_print "${YELLOW}[i]${NC} .env file already exists"
fi

# Initialize database
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Initializing database..."
if python3 -c "
import asyncio
import sys
sys.path.insert(0, '.')
from app.core.database import init_models
asyncio.run(init_models())
print('Database initialized successfully')
" 2>&1 | tee -a "$LOG_FILE"; then
    log_and_print "${GREEN}[✓]${NC} Database initialized"
else
    log_and_print "${YELLOW}[!]${NC} Database may already be initialized"
fi

# Create systemd service
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Creating systemd service..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=CRM Backend Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/.venv/bin"
ExecStart=$APP_DIR/.venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
log_and_print "${GREEN}[✓]${NC} Systemd service created"

# Reload systemd
log_and_print "${YELLOW}[i]${NC} Reloading systemd daemon..."
sudo systemctl daemon-reload
log_and_print "${GREEN}[✓]${NC} Systemd reloaded"

# Enable and start service
log_and_print ""
log_and_print "${YELLOW}[i]${NC} Enabling and starting service..."
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl start ${SERVICE_NAME}
sleep 3
log_and_print "${GREEN}[✓]${NC} Service started"

# Check service status
log_and_print ""
if sudo systemctl is-active --quiet ${SERVICE_NAME}; then
    log_and_print "${GREEN}[✓]${NC} Service is running successfully!"
else
    log_and_print "${RED}[✗]${NC} Service failed to start"
    log_and_print "${YELLOW}[i]${NC} Service status:"
    sudo systemctl status ${SERVICE_NAME} --no-pager | tee -a "$LOG_FILE"
    log_and_print ""
    log_and_print "${YELLOW}[i]${NC} Last 20 lines of service logs:"
    sudo journalctl -u ${SERVICE_NAME} -n 20 --no-pager | tee -a "$LOG_FILE"
fi

# Get IP addresses
LOCAL_IP=$(hostname -I | awk '{print $1}')
PUBLIC_IP=$(curl -s ifconfig.me || echo "Unable to detect")

log_and_print ""
log_and_print "${GREEN}=========================================${NC}"
log_and_print "${GREEN}   Installation Complete!${NC}"
log_and_print "${GREEN}=========================================${NC}"
log_and_print ""
log_and_print "${BLUE}Access URLs:${NC}"
log_and_print "  Local:    ${GREEN}http://localhost:$PORT${NC}"
log_and_print "  Network:  ${GREEN}http://$LOCAL_IP:$PORT${NC}"
if [ "$PUBLIC_IP" != "Unable to detect" ]; then
    log_and_print "  Public:   ${GREEN}http://$PUBLIC_IP:$PORT${NC}"
fi
log_and_print ""
log_and_print "${BLUE}Useful Commands:${NC}"
log_and_print "  Service status:  ${YELLOW}sudo systemctl status ${SERVICE_NAME}${NC}"
log_and_print "  View logs:       ${YELLOW}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
log_and_print "  Restart:         ${YELLOW}sudo systemctl restart ${SERVICE_NAME}${NC}"
log_and_print ""
log_and_print "${BLUE}Installation log saved to:${NC} $LOG_FILE"
log_and_print ""

read -p "Press Enter to exit..."
