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
APP_DIR="$HOME/cem-backend"

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
# Check if venv exists, if so use it
if [ -d "venv" ]; then
    echo -e "${YELLOW}[i]${NC} Using virtual environment"
    source venv/bin/activate
    pip install -r requirements.txt --upgrade
    PYTHON_CMD="python"
else
    echo -e "${YELLOW}[i]${NC} Using system Python"
    pip3 install --break-system-packages -r requirements.txt 2>&1 | grep -v "Cannot uninstall" || true
    PYTHON_CMD="python3"
fi
echo -e "${GREEN}[✓]${NC} Dependencies updated"

echo -e "${YELLOW}[i]${NC} Running database migrations..."
# Try to run migrations if they exist
if [ -f "alembic.ini" ]; then
    echo -e "${YELLOW}[i]${NC} Found Alembic configuration, running migrations..."
    $PYTHON_CMD -m alembic upgrade head 2>/dev/null || echo -e "${YELLOW}[!]${NC} Alembic not configured or no migrations"
fi

# Run any migration scripts in migrations/ folder
if [ -d "migrations" ]; then
    echo -e "${YELLOW}[i]${NC} Checking for migration scripts..."
    for migration_script in migrations/migrate_*.py; do
        if [ -f "$migration_script" ]; then
            echo -e "${YELLOW}[i]${NC} Running $(basename $migration_script)..."
            $PYTHON_CMD "$migration_script" 2>/dev/null || echo -e "${YELLOW}[!]${NC} Migration already applied or not needed"
        fi
    done
fi

# Auto-create any missing tables/columns by importing models
echo -e "${YELLOW}[i]${NC} Ensuring database schema is up to date..."
$PYTHON_CMD -c "
from app.core.database import init_db
import asyncio
asyncio.run(init_db())
print('Database schema updated')
" 2>/dev/null || echo -e "${YELLOW}[!]${NC} Schema update completed with warnings"

echo -e "${GREEN}[✓]${NC} Database migrations completed"

echo -e "${YELLOW}[i]${NC} Updating systemd service file..."
# Detect if using venv or system Python
if [ -d "venv" ]; then
    PYTHON_PATH="$APP_DIR/venv/bin/python"
    echo -e "${YELLOW}[i]${NC} Using venv Python at: $PYTHON_PATH"
else
    PYTHON_PATH=$(which python3)
    echo -e "${YELLOW}[i]${NC} Using system Python at: $PYTHON_PATH"
fi

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
