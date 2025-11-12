#!/bin/bash

###############################################################################
# CRM Backend - Auto Update Cron Setup
# Sets up automatic updates from GitHub on a schedule
###############################################################################

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

APP_DIR="$HOME/crm-backend"

echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}   Setup Auto-Update from GitHub${NC}"
echo -e "${BLUE}=========================================${NC}\n"

if [ ! -d "$APP_DIR" ]; then
    echo -e "${RED}[✗]${NC} CRM Backend not found at $APP_DIR"
    exit 1
fi

echo "Select update frequency:"
echo "  1) Every hour"
echo "  2) Every 6 hours"
echo "  3) Daily at 2 AM"
echo "  4) Daily at specific time"
echo "  5) Weekly (Sunday at 2 AM)"
echo "  6) Custom cron expression"
echo ""
read -p "Choose option (1-6): " choice

case $choice in
    1)
        CRON_EXPR="0 * * * *"
        DESC="every hour"
        ;;
    2)
        CRON_EXPR="0 */6 * * *"
        DESC="every 6 hours"
        ;;
    3)
        CRON_EXPR="0 2 * * *"
        DESC="daily at 2 AM"
        ;;
    4)
        read -p "Enter hour (0-23): " hour
        CRON_EXPR="0 $hour * * *"
        DESC="daily at ${hour}:00"
        ;;
    5)
        CRON_EXPR="0 2 * * 0"
        DESC="weekly on Sunday at 2 AM"
        ;;
    6)
        read -p "Enter cron expression: " CRON_EXPR
        DESC="custom schedule"
        ;;
    *)
        echo "Invalid option"
        exit 1
        ;;
esac

echo ""
echo -e "${YELLOW}[i]${NC} Setting up auto-update: $DESC"

# Create log directory
mkdir -p "$APP_DIR/logs"

# Create cron job
CRON_CMD="$CRON_EXPR cd $APP_DIR && ./update_from_github.sh --auto >> $APP_DIR/logs/auto_update.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "update_from_github.sh"; then
    echo -e "${YELLOW}[!]${NC} Existing auto-update cron job found. Removing..."
    crontab -l | grep -v "update_from_github.sh" | crontab -
fi

# Add new cron job
(crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -

echo -e "${GREEN}[✓]${NC} Auto-update configured: $DESC"
echo ""
echo -e "${BLUE}Cron job added:${NC}"
echo "  $CRON_CMD"
echo ""
echo -e "${BLUE}To view logs:${NC}"
echo "  tail -f $APP_DIR/logs/auto_update.log"
echo ""
echo -e "${BLUE}To manually trigger update:${NC}"
echo "  cd $APP_DIR && ./update_from_github.sh"
echo ""
echo -e "${BLUE}To remove auto-update:${NC}"
echo "  crontab -e  # Remove the line with update_from_github.sh"
echo ""
echo -e "${GREEN}Setup complete!${NC}"
