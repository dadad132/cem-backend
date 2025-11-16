#!/bin/bash

# Ubuntu Server Installation Script for CRM Backend
# Run this on a fresh Ubuntu server

echo "=========================================="
echo "  CRM BACKEND - UBUNTU INSTALLATION"
echo "=========================================="

# Exit on any error
set -e

echo ""
echo "Step 1: Updating system packages..."
sudo apt update
sudo apt upgrade -y

echo ""
echo "Step 2: Installing required packages..."
sudo apt install -y git python3 python3-pip python3-venv curl wget

echo ""
echo "Step 3: Checking Python version..."
python3 --version

echo ""
echo "Step 4: Cloning repository from GitHub..."
cd ~
if [ -d "crm-backend" ]; then
    echo "⚠️  crm-backend directory already exists!"
    echo "Backing up to crm-backend.backup.$(date +%Y%m%d_%H%M%S)"
    mv crm-backend "crm-backend.backup.$(date +%Y%m%d_%H%M%S)"
fi

git clone https://github.com/dadad132/cem-backend.git crm-backend
cd crm-backend

echo ""
echo "Step 5: Creating Python virtual environment..."
python3 -m venv .venv

echo ""
echo "Step 6: Activating virtual environment..."
source .venv/bin/activate

echo ""
echo "Step 7: Upgrading pip..."
pip install --upgrade pip

echo ""
echo "Step 8: Installing Python dependencies..."
pip install -r requirements.txt

echo ""
echo "Step 9: Creating .env file from example..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✅ Created .env file"
    echo ""
    echo "⚠️  IMPORTANT: Edit .env file with your settings:"
    echo "   nano .env"
    echo ""
    echo "Required settings:"
    echo "   - SECRET_KEY (generate random string)"
    echo "   - SMTP_PASSWORD (your email password)"
    echo "   - INCOMING_MAIL_PASSWORD (your email password)"
else
    echo "⚠️  .env file already exists, skipping..."
fi

echo ""
echo "Step 10: Creating necessary directories..."
mkdir -p logs uploads/comments backups

echo ""
echo "Step 11: Setting up database..."
if [ ! -f data.db ]; then
    echo "Creating new database..."
    python -c "from app.core.database import init_db; import asyncio; asyncio.run(init_db())"
    echo "✅ Database created"
else
    echo "⚠️  Database already exists, skipping..."
fi

echo ""
echo "Step 12: Setting up firewall..."
if command -v ufw &> /dev/null; then
    sudo ufw allow 8000/tcp
    sudo ufw allow 22/tcp  # Keep SSH open!
    echo "✅ Firewall configured (ports 8000 and 22 open)"
else
    echo "⚠️  UFW not installed, skipping firewall setup"
fi

echo ""
echo "Step 13: Creating systemd service..."
sudo tee /etc/systemd/system/crm-backend.service > /dev/null <<EOF
[Unit]
Description=CRM Backend Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/crm-backend
Environment="PATH=$HOME/crm-backend/.venv/bin"
ExecStart=$HOME/crm-backend/.venv/bin/python start_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "✅ Systemd service created"

echo ""
echo "Step 14: Enabling and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable crm-backend

echo ""
echo "=========================================="
echo "  INSTALLATION COMPLETE!"
echo "=========================================="

echo ""
echo "Next steps:"
echo ""
echo "1. Edit your .env file:"
echo "   cd ~/crm-backend"
echo "   nano .env"
echo ""
echo "2. Start the service:"
echo "   sudo systemctl start crm-backend"
echo ""
echo "3. Check status:"
echo "   sudo systemctl status crm-backend"
echo ""
echo "4. View logs:"
echo "   sudo journalctl -u crm-backend -f"
echo ""
echo "5. Test the server:"
echo "   curl http://localhost:8000/health"
echo ""
echo "Management commands:"
echo "   sudo systemctl start crm-backend    # Start"
echo "   sudo systemctl stop crm-backend     # Stop"
echo "   sudo systemctl restart crm-backend  # Restart"
echo "   sudo systemctl status crm-backend   # Status"
echo ""
echo "Server will be accessible at: http://YOUR_SERVER_IP:8000"
echo ""
