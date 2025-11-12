#!/bin/bash

###############################################################################
# CRM Backend - Manual Update Script
# Updates application code from a new package
###############################################################################

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

SERVICE_NAME="crm-backend"
APP_DIR="$HOME/crm-backend"

echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}   CRM Backend - Manual Update${NC}"
echo -e "${BLUE}=========================================${NC}\n"

# Check if update package directory is provided
if [ -z "$1" ]; then
    echo -e "${RED}[✗]${NC} No update package directory provided"
    echo ""
    echo "Usage: $0 <path-to-extracted-installer>"
    echo ""
    echo "Example:"
    echo "  1. Extract installer: unzip crm-backend-installer_*.zip"
    echo "  2. Run update: ./update_manual.sh ~/crm-backend-installer_*"
    echo ""
    exit 1
fi

UPDATE_DIR="$1"

if [ ! -d "$UPDATE_DIR" ]; then
    echo -e "${RED}[✗]${NC} Update directory not found: $UPDATE_DIR"
    exit 1
fi

if [ ! -d "$APP_DIR" ]; then
    echo -e "${RED}[✗]${NC} CRM Backend not found at $APP_DIR"
    echo "Please run install_ubuntu.sh first"
    exit 1
fi

echo -e "${YELLOW}[i]${NC} Update source: $UPDATE_DIR"
echo -e "${YELLOW}[i]${NC} Target: $APP_DIR"
echo ""

# Confirm
read -p "Continue with update? (y/N): " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Update cancelled"
    exit 0
fi

echo ""
echo -e "${YELLOW}[1/8]${NC} Stopping service..."
sudo systemctl stop ${SERVICE_NAME}
echo -e "${GREEN}[✓]${NC} Service stopped"

echo -e "${YELLOW}[2/8]${NC} Creating backup..."
BACKUP_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="data_backup_update_${BACKUP_TIMESTAMP}.db"
if [ -f "$APP_DIR/data.db" ]; then
    cp "$APP_DIR/data.db" "$APP_DIR/backups/$BACKUP_FILE"
    echo -e "${GREEN}[✓]${NC} Database backed up to backups/$BACKUP_FILE"
else
    echo -e "${YELLOW}[!]${NC} No database file to backup"
fi

echo -e "${YELLOW}[3/8]${NC} Backing up current installation..."
INSTALL_BACKUP="$HOME/crm-backend.backup.$BACKUP_TIMESTAMP"
mkdir -p "$INSTALL_BACKUP"
cp -r "$APP_DIR/app" "$INSTALL_BACKUP/" 2>/dev/null || true
cp "$APP_DIR/requirements.txt" "$INSTALL_BACKUP/" 2>/dev/null || true
cp "$APP_DIR/start_server.py" "$INSTALL_BACKUP/" 2>/dev/null || true
echo -e "${GREEN}[✓]${NC} Installation backed up to $INSTALL_BACKUP"

echo -e "${YELLOW}[4/8]${NC} Updating application files..."
if [ -d "$UPDATE_DIR/app" ]; then
    cp -r "$UPDATE_DIR/app" "$APP_DIR/"
    echo -e "${GREEN}[✓]${NC} Updated app/ directory"
else
    echo -e "${RED}[✗]${NC} app/ not found in update package"
    exit 1
fi

if [ -f "$UPDATE_DIR/requirements.txt" ]; then
    cp "$UPDATE_DIR/requirements.txt" "$APP_DIR/"
    echo -e "${GREEN}[✓]${NC} Updated requirements.txt"
fi

if [ -f "$UPDATE_DIR/start_server.py" ]; then
    cp "$UPDATE_DIR/start_server.py" "$APP_DIR/"
    echo -e "${GREEN}[✓]${NC} Updated start_server.py"
fi

if [ -f "$UPDATE_DIR/alembic.ini" ]; then
    cp "$UPDATE_DIR/alembic.ini" "$APP_DIR/"
    echo -e "${GREEN}[✓]${NC} Updated alembic.ini"
fi

if [ -d "$UPDATE_DIR/alembic" ]; then
    cp -r "$UPDATE_DIR/alembic" "$APP_DIR/"
    echo -e "${GREEN}[✓]${NC} Updated alembic/ directory"
fi

echo -e "${YELLOW}[5/8]${NC} Activating virtual environment..."
cd "$APP_DIR"
source .venv/bin/activate

echo -e "${YELLOW}[6/8]${NC} Updating dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt --upgrade -q
echo -e "${GREEN}[✓]${NC} Dependencies updated"

echo -e "${YELLOW}[7/8]${NC} Running database migrations..."
python3 -c "
import asyncio
from app.core.database import init_models
asyncio.run(init_models())
" && echo -e "${GREEN}[✓]${NC} Migrations complete" || echo -e "${YELLOW}[!]${NC} Migration warning (may be ok)"

echo -e "${YELLOW}[8/8]${NC} Starting service..."
sudo systemctl start ${SERVICE_NAME}
sleep 3

if sudo systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}[✓]${NC} Service started successfully"
else
    echo -e "${RED}[✗]${NC} Service failed to start"
    echo ""
    echo "Rolling back to previous version..."
    sudo systemctl stop ${SERVICE_NAME}
    cp -r "$INSTALL_BACKUP/app" "$APP_DIR/"
    cp "$INSTALL_BACKUP/requirements.txt" "$APP_DIR/"
    cp "$INSTALL_BACKUP/start_server.py" "$APP_DIR/"
    sudo systemctl start ${SERVICE_NAME}
    echo -e "${YELLOW}[!]${NC} Rolled back. Check logs: sudo journalctl -u ${SERVICE_NAME} -f"
    exit 1
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   Update Complete!${NC}"
echo -e "${GREEN}=========================================${NC}\n"

echo -e "${BLUE}Backup locations:${NC}"
echo "  Database: $APP_DIR/backups/$BACKUP_FILE"
echo "  Installation: $INSTALL_BACKUP"
echo ""
echo -e "${BLUE}To verify:${NC}"
echo "  sudo systemctl status crm-backend"
echo "  sudo journalctl -u crm-backend -f"
echo "  curl http://localhost:8000/api/system/version"
echo ""
