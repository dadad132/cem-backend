#!/bin/bash

###############################################################################
# CRM Backend - Update Script
# Automatically pulls latest code and restarts the server
###############################################################################

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SERVICE_NAME="crm-backend"
APP_DIR="$HOME/crm-backend"

echo -e "${YELLOW}=========================================${NC}"
echo -e "${YELLOW}   CRM Backend - Auto Update${NC}"
echo -e "${YELLOW}=========================================${NC}\n"

# Check if running from app directory
if [ ! -d "$APP_DIR" ]; then
    echo -e "${RED}[✗]${NC} Application directory not found: $APP_DIR"
    exit 1
fi

cd "$APP_DIR"

echo -e "${YELLOW}[i]${NC} Checking for updates..."

# Get current version
CURRENT_VERSION=$(python3 -c "from app.core.version import VERSION; print(VERSION)" 2>/dev/null || echo "unknown")
echo -e "${GREEN}[✓]${NC} Current version: $CURRENT_VERSION"

# Create backup before update
echo -e "${YELLOW}[i]${NC} Creating backup..."
BACKUP_FILE="data_backup_update_$(date +%Y%m%d_%H%M%S).db"
if [ -f "data.db" ]; then
    cp data.db "backups/$BACKUP_FILE"
    echo -e "${GREEN}[✓]${NC} Database backed up to backups/$BACKUP_FILE"
fi

# Stop the service
echo -e "${YELLOW}[i]${NC} Stopping service..."
sudo systemctl stop ${SERVICE_NAME}
echo -e "${GREEN}[✓]${NC} Service stopped"

# Pull latest code (if using git)
if [ -d ".git" ]; then
    echo -e "${YELLOW}[i]${NC} Pulling latest code from repository..."
    git pull origin main || git pull origin master
    echo -e "${GREEN}[✓]${NC} Code updated"
else
    echo -e "${YELLOW}[!]${NC} Not a git repository. Manual update required."
fi

# Activate virtual environment and update dependencies
echo -e "${YELLOW}[i]${NC} Updating dependencies..."
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt --upgrade -q
echo -e "${GREEN}[✓]${NC} Dependencies updated"

# Run database migrations if any
echo -e "${YELLOW}[i]${NC} Running database migrations..."

# Run table creation if needed
python3 -c "
import asyncio
from app.core.database import init_models
asyncio.run(init_models())
print('Database models initialized')
" 2>/dev/null || echo -e "${YELLOW}[!]${NC} Model initialization completed with warnings"

# Run any migration scripts found
if [ -f "migrate_profile_picture_simple.py" ]; then
    echo -e "${YELLOW}[i]${NC} Running profile picture migration..."
    python3 migrate_profile_picture_simple.py 2>/dev/null || echo -e "${YELLOW}[!]${NC} Profile picture migration may have already been applied"
fi

if [ -f "migrate_task_archive.py" ]; then
    echo -e "${YELLOW}[i]${NC} Running task archive migration..."
    python3 migrate_task_archive.py 2>/dev/null || echo -e "${YELLOW}[!]${NC} Task archive migration may have already been applied"
fi

if [ -f "migrate_meeting_cancellation.py" ]; then
    echo -e "${YELLOW}[i]${NC} Running meeting cancellation migration..."
    python3 migrate_meeting_cancellation.py 2>/dev/null || echo -e "${YELLOW}[!]${NC} Meeting cancellation migration may have already been applied"
fi

echo -e "${GREEN}[✓]${NC} Database migrations complete"

# Get new version
NEW_VERSION=$(python3 -c "from app.core.version import VERSION; print(VERSION)" 2>/dev/null || echo "unknown")

# Start the service
echo -e "${YELLOW}[i]${NC} Starting service..."
sudo systemctl start ${SERVICE_NAME}
sleep 3

# Check if service started successfully
if sudo systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}[✓]${NC} Service started successfully"
else
    echo -e "${RED}[✗]${NC} Service failed to start"
    echo -e "${YELLOW}[i]${NC} Attempting to restore from backup..."
    cp "backups/$BACKUP_FILE" data.db
    sudo systemctl start ${SERVICE_NAME}
    exit 1
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   Update Complete!${NC}"
echo -e "${GREEN}=========================================${NC}\n"

echo -e "${GREEN}Previous version:${NC} $CURRENT_VERSION"
echo -e "${GREEN}Current version:${NC}  $NEW_VERSION"
echo ""
echo -e "${GREEN}Service status:${NC} $(sudo systemctl is-active ${SERVICE_NAME})"
echo -e "${GREEN}Check logs:${NC} sudo journalctl -u ${SERVICE_NAME} -n 50"
echo ""
