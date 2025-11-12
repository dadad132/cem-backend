#!/bin/bash
# Setup script for Ubuntu deployment

echo "=================================="
echo "CRM Backend - Ubuntu Setup"
echo "=================================="
echo ""

# Update system packages
echo "📦 Updating system packages..."
sudo apt update
sudo apt upgrade -y

# Install Python 3.12 if not installed
echo "🐍 Checking Python installation..."
if ! command -v python3.12 &> /dev/null; then
    echo "Installing Python 3.12..."
    sudo apt install -y software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt update
    sudo apt install -y python3.12 python3.12-venv python3.12-dev
else
    echo "✓ Python 3.12 already installed"
fi

# Install pip for Python 3.12
echo "📥 Installing pip..."
sudo apt install -y python3-pip

# Create virtual environment
echo "🔧 Creating virtual environment..."
python3.12 -m venv venv

# Activate virtual environment
echo "✅ Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "📚 Installing Python packages..."
pip install -r requirements.txt

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p backups
mkdir -p logs

# Set permissions
echo "🔐 Setting permissions..."
chmod +x start_server.py
chmod 755 backups
chmod 755 logs

echo ""
echo "=================================="
echo "✅ Setup Complete!"
echo "=================================="
echo ""
echo "To start the server:"
echo "  1. Activate virtual environment: source venv/bin/activate"
echo "  2. Run the server: python start_server.py"
echo ""
echo "To run in background (production):"
echo "  nohup python start_server.py > logs/server.log 2>&1 &"
echo ""
