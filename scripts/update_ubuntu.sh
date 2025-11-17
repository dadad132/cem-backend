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
sudo systemctl stop ${SERVICE_NAME}
echo -e "${GREEN}[✓]${NC} Service stopped"

echo -e "${YELLOW}[i]${NC} Creating backup..."
BACKUP_FILE="data_backup_update_$(date +%Y%m%d_%H%M%S).db"
if [ -f "data.db" ]; then
    cp data.db "backups/$BACKUP_FILE"
    echo -e "${GREEN}[✓]${NC} Database backed up to backups/$BACKUP_FILE"
fi

echo -e "${YELLOW}[i]${NC} Updating from GitHub..."
git fetch origin
git reset --hard origin/main
echo -e "${GREEN}[✓]${NC} Code updated"

echo -e "${YELLOW}[i]${NC} Activating virtual environment..."
source .venv/bin/activate

echo -e "${YELLOW}[i]${NC} Updating dependencies..."
pip install --upgrade pip
pip install -r requirements.txt --upgrade

echo -e "${YELLOW}[i]${NC} Running database initialization..."
# Ensure all tables exist
python3 -c "
import asyncio
from app.core.database import init_models
asyncio.run(init_models())
" || echo -e "${YELLOW}[!]${NC} Model initialization completed with warnings"

echo -e "${YELLOW}[i]${NC} Starting service..."
sudo systemctl start ${SERVICE_NAME}
sleep 2

if sudo systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}[✓]${NC} Service restarted successfully"
else
    echo -e "${RED}[✗]${NC} Service failed to start"
    echo "Check logs: sudo journalctl -u ${SERVICE_NAME} -f"
    exit 1
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   Update Complete!${NC}"
echo -e "${GREEN}=========================================${NC}\n"
