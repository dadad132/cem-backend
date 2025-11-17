#!/bin/bash

###############################################################################
# CRM Backend - Simple Ubuntu Installer with Full Logging
###############################################################################

LOGFILE="install_debug.log"
exec 1> >(tee -a "$LOGFILE")
exec 2>&1

set -x  # Print every command

echo "=== CRM Backend Installation Started ==="
echo "Log file: $LOGFILE"

# Configuration
APP_NAME="crm-backend"
SERVICE_NAME="crm-backend"
PORT=8000

# Determine installation directory
if [ "$EUID" -eq 0 ]; then 
    echo "Running as root"
    APP_DIR="/opt/$APP_NAME"
    SERVICE_USER="${SUDO_USER:-www-data}"
else
    echo "Running as regular user"
    APP_DIR="$HOME/$APP_NAME"
    SERVICE_USER="$USER"
fi

echo "APP_DIR: $APP_DIR"
echo "SERVICE_USER: $SERVICE_USER"

# Update system
echo "Updating system..."
sudo apt update

# Install dependencies
echo "Installing dependencies..."
sudo apt install -y python3 python3-pip git curl sqlite3

# Remove old directory if exists
if [ -d "$APP_DIR" ]; then
    echo "Removing old installation..."
    rm -rf "$APP_DIR"
fi

# Clone repository
echo "Cloning repository to: $APP_DIR"
git clone https://github.com/dadad132/cem-backend.git "$APP_DIR"

if [ ! -d "$APP_DIR" ]; then
    echo "ERROR: Failed to create $APP_DIR"
    exit 1
fi

cd "$APP_DIR" || exit 1
echo "Current directory: $(pwd)"

# Install Python packages
echo "Installing Python packages..."
pip3 install --break-system-packages -r requirements.txt

# Create directories
echo "Creating directories..."
mkdir -p logs backups app/uploads/comments updates

# Create .env file
if [ ! -f ".env" ]; then
    echo "Creating .env file..."
    cat > .env << 'EOF'
DATABASE_URL=sqlite+aiosqlite:///./data.db
SECRET_KEY=change-this-secret-key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
HOST=0.0.0.0
PORT=8000
EOF
fi

# Initialize database
echo "Initializing database..."
python3 << 'PYEOF'
import asyncio
import sys
sys.path.insert(0, '.')
from app.core.database import init_models
asyncio.run(init_models())
print('Database initialized')
PYEOF

# Create systemd service
echo "Creating systemd service..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=CRM Backend Service
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Start service
echo "Starting service..."
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl start ${SERVICE_NAME}
sleep 3

# Check status
echo "Checking service status..."
sudo systemctl status ${SERVICE_NAME} --no-pager

echo ""
echo "=== Installation Complete ==="
echo "Check the log file: cat $LOGFILE"
echo "Service logs: sudo journalctl -u ${SERVICE_NAME} -n 50"
echo "Access at: http://localhost:$PORT"
