#!/bin/bash

###############################################################################
# CRM Backend - Complete Setup Script (No venv)
# Run this to install or fix your installation
###############################################################################

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

APP_DIR="$HOME/cem-backend"
SERVICE_NAME="crm-backend"
PORT=8000

echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   CRM Backend - Complete Setup${NC}"
echo -e "${GREEN}=========================================${NC}\n"

# Check if directory exists
if [ ! -d "$APP_DIR" ]; then
    echo -e "${YELLOW}[i]${NC} Cloning repository..."
    git clone https://github.com/dadad132/cem-backend.git "$APP_DIR"
    cd "$APP_DIR"
else
    echo -e "${YELLOW}[i]${NC} Directory exists, updating..."
    cd "$APP_DIR"
    git fetch origin
    git reset --hard origin/main
fi

echo -e "${GREEN}[✓]${NC} Code ready"

# Install system dependencies
echo -e "${YELLOW}[i]${NC} Installing system dependencies..."
sudo apt update
sudo apt install -y python3 python3-pip git sqlite3
echo -e "${GREEN}[✓]${NC} System dependencies installed"

# Install Python packages
echo -e "${YELLOW}[i]${NC} Installing Python packages..."
pip3 install --break-system-packages sqlmodel aiosqlite python-multipart python-jose passlib bcrypt httpx email-validator python-dotenv pydantic-settings jinja2 2>&1 | grep -v "Cannot uninstall" || true
echo -e "${GREEN}[✓]${NC} Python packages installed"

# Create directories
echo -e "${YELLOW}[i]${NC} Creating directories..."
mkdir -p logs backups app/uploads/comments updates
echo -e "${GREEN}[✓]${NC} Directories created"

# Create .env file with random secret key
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}[i]${NC} Creating .env file..."
    SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || echo "change-this-secret-key-$(date +%s)")
    cat > .env << EOF
DATABASE_URL=sqlite+aiosqlite:///./data.db
SECRET_KEY=$SECRET_KEY
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
HOST=0.0.0.0
PORT=8000
EOF
    echo -e "${GREEN}[✓]${NC} .env file created"
else
    echo -e "${YELLOW}[i]${NC} .env file already exists"
fi

# Get Python path
PYTHON_PATH=$(which python3)
echo -e "${YELLOW}[i]${NC} Using Python at: $PYTHON_PATH"

# Create systemd service
echo -e "${YELLOW}[i]${NC} Creating systemd service..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=CRM Backend Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON_PATH -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
echo -e "${GREEN}[✓]${NC} Service file created"

# Reload and start service
echo -e "${YELLOW}[i]${NC} Starting service..."
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl stop ${SERVICE_NAME} 2>/dev/null || true
sleep 1
sudo systemctl start ${SERVICE_NAME}
sleep 2

# Check status
if sudo systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}[✓]${NC} Service is running!"
    echo ""
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}   Setup Complete!${NC}"
    echo -e "${GREEN}=========================================${NC}\n"
    echo "Access your CRM at: http://localhost:$PORT"
    echo ""
    echo "Useful commands:"
    echo "  View logs:    sudo journalctl -u ${SERVICE_NAME} -f"
    echo "  Restart:      sudo systemctl restart ${SERVICE_NAME}"
    echo "  Stop:         sudo systemctl stop ${SERVICE_NAME}"
    echo "  Status:       sudo systemctl status ${SERVICE_NAME}"
    echo ""
else
    echo -e "${RED}[✗]${NC} Service failed to start!"
    echo ""
    echo "Check the error with:"
    echo "  sudo journalctl -u ${SERVICE_NAME} -n 50"
    echo ""
    echo "Or run manually to see errors:"
    echo "  cd $APP_DIR"
    echo "  python3 -m uvicorn app.main:app"
    exit 1
fi
