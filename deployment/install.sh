#!/bin/bash
set -e

# CRM Backend Installation Script for Ubuntu
# This script installs and configures the CRM application on a fresh Ubuntu server

echo "============================================================"
echo "CRM Backend Installation Script"
echo "============================================================"
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "❌ This script must be run as root (use sudo)" 
   exit 1
fi

# Configuration
INSTALL_DIR="/opt/crm-backend"
APP_USER="crm"
APP_GROUP="crm"
PYTHON_VERSION="python3"

echo "📦 Installation Configuration:"
echo "   Install Directory: $INSTALL_DIR"
echo "   Application User: $APP_USER"
echo "   Python Version: $PYTHON_VERSION"
echo ""

# Step 1: Update system
echo "📥 Step 1: Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# Step 2: Install dependencies
echo "📦 Step 2: Installing system dependencies..."
apt-get install -y -qq \
    python3 \
    python3-pip \
    python3-venv \
    sqlite3 \
    nginx \
    supervisor \
    git \
    curl \
    ufw

echo "✅ System dependencies installed"

# Step 3: Create application user
echo "👤 Step 3: Creating application user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --shell /bin/bash --home-dir $INSTALL_DIR --create-home $APP_USER
    echo "✅ User '$APP_USER' created"
else
    echo "✅ User '$APP_USER' already exists"
fi

# Step 4: Create directory structure
echo "📁 Step 4: Creating directory structure..."
mkdir -p $INSTALL_DIR
mkdir -p $INSTALL_DIR/backups
mkdir -p $INSTALL_DIR/uploads
mkdir -p $INSTALL_DIR/logs

# Step 5: Copy application files
echo "📋 Step 5: Copying application files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"

# Copy all files except venv and __pycache__
rsync -av --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.git' --exclude='data.db' --exclude='backups' \
    "$PARENT_DIR/" "$INSTALL_DIR/"

echo "✅ Application files copied"

# Step 6: Create Python virtual environment
echo "🐍 Step 6: Creating Python virtual environment..."
cd $INSTALL_DIR
python3 -m venv venv
source venv/bin/activate

echo "📦 Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "✅ Python environment configured"

# Step 7: Set proper permissions
echo "🔐 Step 7: Setting file permissions..."
chown -R $APP_USER:$APP_GROUP $INSTALL_DIR
chmod -R 755 $INSTALL_DIR
chmod -R 770 $INSTALL_DIR/backups
chmod -R 770 $INSTALL_DIR/uploads
chmod -R 770 $INSTALL_DIR/logs

echo "✅ Permissions set"

# Step 8: Configure firewall
echo "🔥 Step 8: Configuring firewall..."
ufw --force enable
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw allow 8000/tcp  # Application port

echo "✅ Firewall configured"

# Step 9: Install systemd service
echo "⚙️  Step 9: Installing systemd service..."
cp $INSTALL_DIR/deployment/crm-backend.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable crm-backend.service

echo "✅ Systemd service installed"

# Step 10: Create management scripts
echo "📝 Step 10: Creating management scripts..."

# Start script
cat > /usr/local/bin/crm-start <<'EOF'
#!/bin/bash
echo "🚀 Starting CRM Backend..."
sudo systemctl start crm-backend.service
sleep 2
sudo systemctl status crm-backend.service --no-pager
EOF
chmod +x /usr/local/bin/crm-start

# Stop script
cat > /usr/local/bin/crm-stop <<'EOF'
#!/bin/bash
echo "🛑 Stopping CRM Backend (graceful shutdown)..."
sudo systemctl stop crm-backend.service
echo "✅ CRM Backend stopped safely"
EOF
chmod +x /usr/local/bin/crm-stop

# Restart script
cat > /usr/local/bin/crm-restart <<'EOF'
#!/bin/bash
echo "🔄 Restarting CRM Backend..."
sudo systemctl restart crm-backend.service
sleep 2
sudo systemctl status crm-backend.service --no-pager
EOF
chmod +x /usr/local/bin/crm-restart

# Status script
cat > /usr/local/bin/crm-status <<'EOF'
#!/bin/bash
sudo systemctl status crm-backend.service --no-pager
echo ""
echo "Logs (last 20 lines):"
sudo journalctl -u crm-backend.service -n 20 --no-pager
EOF
chmod +x /usr/local/bin/crm-status

# Logs script
cat > /usr/local/bin/crm-logs <<'EOF'
#!/bin/bash
sudo journalctl -u crm-backend.service -f
EOF
chmod +x /usr/local/bin/crm-logs

# Backup script
cat > /usr/local/bin/crm-backup <<'EOF'
#!/bin/bash
echo "💾 Creating manual backup..."
cd /opt/crm-backend
sudo -u crm ./venv/bin/python -c "from app.core.backup import backup_manager; print(f'Backup created: {backup_manager.create_backup(is_manual=True)}')"
EOF
chmod +x /usr/local/bin/crm-backup

echo "✅ Management scripts created"

# Step 11: Display completion message
echo ""
echo "============================================================"
echo "✅ Installation Complete!"
echo "============================================================"
echo ""
echo "🎯 Management Commands:"
echo "   crm-start    - Start the application"
echo "   crm-stop     - Stop the application (safe)"
echo "   crm-restart  - Restart the application"
echo "   crm-status   - Check application status"
echo "   crm-logs     - View live logs"
echo "   crm-backup   - Create manual backup"
echo ""
echo "📍 Application Directory: $INSTALL_DIR"
echo "🌐 Access URL: http://$(hostname -I | awk '{print $1}'):8000"
echo ""
echo "⚠️  IMPORTANT: First-time setup required"
echo "   1. Start the application: sudo crm-start"
echo "   2. Navigate to the web interface"
echo "   3. Create your first admin user account"
echo ""
echo "📚 View logs: sudo crm-logs"
echo "📊 Check status: sudo crm-status"
echo ""
echo "============================================================"
