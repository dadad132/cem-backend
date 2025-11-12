#!/bin/bash

###############################################################################
# CRM Backend - GitHub Auto-Update Script
# Automatically fetches and installs updates from GitHub
#
# PRIVATE REPOSITORY AUTHENTICATION:
# For private GitHub repos, you need a Personal Access Token (PAT):
# 1. Create PAT: GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
# 2. Generate new token with 'repo' scope
# 3. Configure Git to use it:
#    git config --global credential.helper store
#    git pull (enter username and PAT when prompted)
# The credentials will be saved for future use.
###############################################################################

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

SERVICE_NAME="crm-backend"
APP_DIR="$HOME/crm-backend"
GITHUB_REPO="dadad132/cem-backend"
GITHUB_BRANCH="main"

echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}   CRM Backend - GitHub Auto Update${NC}"
echo -e "${BLUE}=========================================${NC}\n"

if [ ! -d "$APP_DIR" ]; then
    echo -e "${RED}[✗]${NC} CRM Backend not found at $APP_DIR"
    echo "Please run install_ubuntu.sh first"
    exit 1
fi

cd "$APP_DIR"

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo -e "${YELLOW}[i]${NC} Installing git..."
    sudo apt update
    sudo apt install -y git
fi

# Initialize git if needed
if [ ! -d ".git" ]; then
    echo -e "${YELLOW}[i]${NC} Initializing git repository..."
    git init
    git remote add origin "https://github.com/${GITHUB_REPO}.git"
fi

echo -e "${YELLOW}[i]${NC} Checking for updates from GitHub..."
git fetch origin "$GITHUB_BRANCH" --quiet 2>/dev/null || {
    echo -e "${RED}[✗]${NC} Failed to fetch from GitHub"
    echo "Possible causes:"
    echo "  - No internet connection"
    echo "  - GitHub authentication required (private repo)"
    echo ""
    echo "For private repos, configure credentials:"
    echo "  git config credential.helper store"
    echo "  git pull  # Enter username and Personal Access Token"
    exit 1
}

# Check if there are updates
LOCAL_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "none")
REMOTE_COMMIT=$(git rev-parse "origin/$GITHUB_BRANCH" 2>/dev/null || echo "none")

if [ "$LOCAL_COMMIT" = "$REMOTE_COMMIT" ]; then
    echo -e "${GREEN}[✓]${NC} Already up to date!"
    exit 0
fi

echo -e "${YELLOW}[!]${NC} Updates available!"
echo "  Current: ${LOCAL_COMMIT:0:7}"
echo "  New:     ${REMOTE_COMMIT:0:7}"
echo ""

# Confirm update
if [ "$1" != "--auto" ]; then
    read -p "Install updates? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Update cancelled"
        exit 0
    fi
fi

echo ""
echo -e "${YELLOW}[1/9]${NC} Stopping service..."
sudo systemctl stop ${SERVICE_NAME}
echo -e "${GREEN}[✓]${NC} Service stopped"

echo -e "${YELLOW}[2/9]${NC} Creating backup..."
BACKUP_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="data_backup_update_${BACKUP_TIMESTAMP}.db"

if [ -f "data.db" ]; then
    cp "data.db" "backups/$BACKUP_FILE"
    echo -e "${GREEN}[✓]${NC} Database backed up to backups/$BACKUP_FILE"
else
    echo -e "${YELLOW}[!]${NC} No database file to backup"
fi

echo -e "${YELLOW}[3/9]${NC} Backing up .env file..."
if [ -f ".env" ]; then
    cp .env .env.backup.$BACKUP_TIMESTAMP
    echo -e "${GREEN}[✓]${NC} .env backed up"
fi

echo -e "${YELLOW}[4/9]${NC} Fetching latest changes..."
git fetch origin "$GITHUB_BRANCH" || {
    echo -e "${RED}[✗]${NC} Failed to fetch from GitHub"
    echo "Check your internet connection and GitHub authentication"
    sudo systemctl start ${SERVICE_NAME}
    exit 1
}

echo -e "${YELLOW}[5/9]${NC} Cleaning untracked files..."
# Remove untracked files (except .env, .db, backups, logs, uploads)
git clean -fd -e .env -e '*.db' -e backups/ -e logs/ -e uploads/ -e .venv/ -e venv/
echo -e "${GREEN}[✓]${NC} Cleaned duplicates and temp files"

echo -e "${YELLOW}[6/9]${NC} Applying updates (discarding local changes)..."
# Reset to match remote exactly (discard local changes to tracked files)
git reset --hard "origin/$GITHUB_BRANCH" || {
    echo -e "${RED}[✗]${NC} Failed to reset to remote version"
    sudo systemctl start ${SERVICE_NAME}
    exit 1
}
echo -e "${GREEN}[✓]${NC} Code updated from GitHub"

echo -e "${YELLOW}[7/9]${NC} Restoring .env if needed..."
if [ -f ".env.backup.$BACKUP_TIMESTAMP" ] && [ ! -f ".env" ]; then
    cp .env.backup.$BACKUP_TIMESTAMP .env
    echo -e "${GREEN}[✓]${NC} .env restored"
fi

echo -e "${YELLOW}[8/9]${NC} Updating dependencies..."
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    echo -e "  ${BLUE}→${NC} Upgrading pip..."
    pip install --upgrade pip -q
    echo -e "  ${BLUE}→${NC} Installing/upgrading all requirements..."
    pip install -r requirements.txt --upgrade -q
    echo -e "${GREEN}[✓]${NC} Dependencies updated in .venv"
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    echo -e "  ${BLUE}→${NC} Upgrading pip..."
    pip install --upgrade pip -q
    echo -e "  ${BLUE}→${NC} Installing/upgrading all requirements..."
    pip install -r requirements.txt --upgrade -q
    echo -e "${GREEN}[✓]${NC} Dependencies updated in venv"
else
    # No venv found, use system Python
    echo -e "  ${BLUE}→${NC} Upgrading pip..."
    pip3 install --upgrade pip -q --user
    echo -e "  ${BLUE}→${NC} Installing/upgrading all requirements..."
    pip3 install -r requirements.txt --upgrade -q --user
    echo -e "${GREEN}[✓]${NC} Dependencies updated (system Python)"
fi

echo -e "${YELLOW}[9/11]${NC} Running database migrations..."
python3 -c "
import asyncio
from app.core.database import init_models
asyncio.run(init_models())
" && echo -e "${GREEN}[✓]${NC} Base migrations complete" || echo -e "${YELLOW}[!]${NC} Migration warning (may be ok)"

echo -e "${YELLOW}[10/11]${NC} Running migration scripts..."
# Run all migration scripts in order
MIGRATION_SCRIPTS=(
    "migrate_google_oauth.py"
    "migrate_subtasks.py"
    "migrate_notifications_interactive.py"
    "migrate_project_archive.py"
    "migrate_ticket_system.py"
    "migrate_guest_tickets_combined.py"
    "migrate_processed_mail.py"
    "migrate_calendar_features.py"
)

for script in "${MIGRATION_SCRIPTS[@]}"; do
    if [ -f "$script" ]; then
        echo -e "  ${BLUE}→${NC} Running $script..."
        python3 "$script" 2>&1 | grep -E "✓|✅|Migration completed|already exists|Added" || true
    fi
done
echo -e "${GREEN}[✓]${NC} Migration scripts complete"

echo -e "${YELLOW}[11/11]${NC} Starting service..."
sudo systemctl start ${SERVICE_NAME}
sleep 3

if sudo systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}[✓]${NC} Service started successfully"
else
    echo -e "${RED}[✗]${NC} Service failed to start"
    echo ""
    echo "Rolling back..."
    git reset --hard HEAD~1
    if [ -f ".env.backup.$BACKUP_TIMESTAMP" ]; then
        cp .env.backup.$BACKUP_TIMESTAMP .env
    fi
    sudo systemctl start ${SERVICE_NAME}
    echo -e "${YELLOW}[!]${NC} Rolled back. Check logs: sudo journalctl -u ${SERVICE_NAME} -f"
    exit 1
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   Update Complete!${NC}"
echo -e "${GREEN}=========================================${NC}\n"

NEW_VERSION=$(git rev-parse --short HEAD)
echo -e "${BLUE}New version:${NC} $NEW_VERSION"
echo -e "${BLUE}Backup:${NC} backups/$BACKUP_FILE"
echo ""
echo -e "${BLUE}To verify:${NC}"
echo "  sudo systemctl status crm-backend"
echo "  curl http://localhost:8000/api/system/version"
echo ""
