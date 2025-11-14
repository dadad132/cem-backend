#!/bin/bash

###############################################################################
# CRM Backend - Quick Uninstaller for Ubuntu
###############################################################################

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SERVICE_NAME="crm-backend"
APP_DIR="$HOME/crm-backend"

echo -e "${YELLOW}=========================================${NC}"
echo -e "${YELLOW}   CRM Backend - Uninstaller${NC}"
echo -e "${YELLOW}=========================================${NC}\n"

echo -e "${RED}WARNING: This will completely remove the CRM backend!${NC}"
echo -e "This includes:"
echo -e "  - Service configuration"
echo -e "  - Application files"
echo -e "  - Database (all data will be lost!)"
echo -e "  - Backups"
echo ""

read -p "Are you sure you want to continue? (yes/no): " -r
echo
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo -e "${GREEN}Uninstall cancelled.${NC}"
    exit 0
fi

echo -e "${YELLOW}[i]${NC} Starting uninstallation..."

# Stop service
if sudo systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${YELLOW}[i]${NC} Stopping service..."
    sudo systemctl stop ${SERVICE_NAME}
    echo -e "${GREEN}[✓]${NC} Service stopped"
fi

# Disable service
if sudo systemctl is-enabled --quiet ${SERVICE_NAME} 2>/dev/null; then
    echo -e "${YELLOW}[i]${NC} Disabling service..."
    sudo systemctl disable ${SERVICE_NAME}
    echo -e "${GREEN}[✓]${NC} Service disabled"
fi

# Remove service file
if [ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]; then
    echo -e "${YELLOW}[i]${NC} Removing service file..."
    sudo rm /etc/systemd/system/${SERVICE_NAME}.service
    sudo systemctl daemon-reload
    echo -e "${GREEN}[✓]${NC} Service file removed"
fi

# Remove application directory
if [ -d "$APP_DIR" ]; then
    echo -e "${YELLOW}[i]${NC} Removing application directory..."
    rm -rf "$APP_DIR"
    echo -e "${GREEN}[✓]${NC} Application removed"
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   Uninstallation Complete!${NC}"
echo -e "${GREEN}=========================================${NC}\n"
echo -e "${GREEN}CRM Backend has been completely removed from your system.${NC}\n"
