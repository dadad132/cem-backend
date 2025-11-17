#!/bin/bash

###############################################################################
# CRM Backend - Update Script
# Updates an existing installation to the latest version
###############################################################################

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SERVICE_NAME="crm-backend"
APP_DIR="$HOME/crm-backend"

echo -e "${YELLOW}=========================================${NC}"
echo -e "${YELLOW}   CRM Backend - Update${NC}"
echo -e "${YELLOW}=========================================${NC}\n"

if [ ! -d "$APP_DIR" ]; then
    echo -e "${RED}[✗]${NC} CRM Backend not found at $APP_DIR"
    echo "Please run install_ubuntu.sh first"
    exit 1
fi

cd "$APP_DIR"

echo -e "${YELLOW}[i]${NC} Stopping service..."
sudo systemctl stop ${SERVICE_NAME} 2>/dev/null || echo -e "${YELLOW}[!]${NC} Service not running"
echo -e "${GREEN}[✓]${NC} Service stopped"

echo -e "${YELLOW}[i]${NC} Creating backup..."
mkdir -p backups
BACKUP_FILE="data_backup_update_$(date +%Y%m%d_%H%M%S).db"
if [ -f "data.db" ]; then
    cp data.db "backups/$BACKUP_FILE"
    echo -e "${GREEN}[✓]${NC} Database backed up to backups/$BACKUP_FILE"
fi

echo -e "${YELLOW}[i]${NC} Updating from GitHub..."
git fetch origin
git reset --hard origin/main
echo -e "${GREEN}[✓]${NC} Code updated"

echo -e "${YELLOW}[i]${NC} Updating dependencies..."
pip3 install --break-system-packages -r requirements.txt 2>&1 | grep -v "Cannot uninstall" || true
echo -e "${GREEN}[✓]${NC} Dependencies updated"

echo -e "${YELLOW}[i]${NC} Updating systemd service file..."
# Get the actual Python path
PYTHON_PATH=$(which python3)
echo -e "${YELLOW}[i]${NC} Using Python at: $PYTHON_PATH"

sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=CRM Backend Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON_PATH -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
echo -e "${GREEN}[✓]${NC} Service file updated"

echo -e "${YELLOW}[i]${NC} Reloading systemd..."
sudo systemctl daemon-reload
echo -e "${GREEN}[✓]${NC} Systemd reloaded"

echo -e "${YELLOW}[i]${NC} Starting service..."
sudo systemctl start ${SERVICE_NAME}
sleep 2

if sudo systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}[✓]${NC} Service started successfully"
else
    echo -e "${RED}[✗]${NC} Service failed to start"
    echo "Check logs: sudo journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   Update Complete!${NC}"
echo -e "${GREEN}=========================================${NC}\n"
echo "Service is running. Access at: http://localhost:8000"
echo ""
echo "Useful commands:"
echo "  View logs: sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Restart:   sudo systemctl restart ${SERVICE_NAME}"
echo "  Status:    sudo systemctl status ${SERVICE_NAME}"
echo ""
