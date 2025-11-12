#!/bin/bash

###############################################################################
# CRM Backend - Debian Package Builder
# Creates a .deb package for easy installation on Ubuntu/Debian systems
###############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}   Creating Debian Package${NC}"
echo -e "${BLUE}=========================================${NC}\n"

# Configuration
APP_NAME="crm-backend"
VERSION="1.2.0"
ARCHITECTURE="all"
MAINTAINER="CRM Backend Team <admin@example.com>"
DESCRIPTION="CRM Backend - Complete customer relationship management system"

# Build directories
BUILD_DIR="build/deb"
PACKAGE_NAME="${APP_NAME}_${VERSION}_${ARCHITECTURE}"
PACKAGE_DIR="$BUILD_DIR/$PACKAGE_NAME"

# Clean previous build
echo -e "${YELLOW}[i]${NC} Cleaning previous build..."
rm -rf "$BUILD_DIR"

# Create package structure
echo -e "${YELLOW}[i]${NC} Creating package structure..."
mkdir -p "$PACKAGE_DIR/DEBIAN"
mkdir -p "$PACKAGE_DIR/opt/$APP_NAME"
mkdir -p "$PACKAGE_DIR/etc/systemd/system"
mkdir -p "$PACKAGE_DIR/usr/local/bin"

# Copy application files
echo -e "${YELLOW}[i]${NC} Copying application files..."
cp -r app "$PACKAGE_DIR/opt/$APP_NAME/"
cp -r alembic "$PACKAGE_DIR/opt/$APP_NAME/"
cp requirements.txt "$PACKAGE_DIR/opt/$APP_NAME/"
cp alembic.ini "$PACKAGE_DIR/opt/$APP_NAME/"
cp start_server.py "$PACKAGE_DIR/opt/$APP_NAME/"

# Create necessary directories
mkdir -p "$PACKAGE_DIR/opt/$APP_NAME/logs"
mkdir -p "$PACKAGE_DIR/opt/$APP_NAME/backups"
mkdir -p "$PACKAGE_DIR/opt/$APP_NAME/app/uploads/comments"
mkdir -p "$PACKAGE_DIR/opt/$APP_NAME/updates"

# Create .env template
echo -e "${YELLOW}[i]${NC} Creating configuration template..."
cat > "$PACKAGE_DIR/opt/$APP_NAME/.env.example" << 'EOF'
# CRM Backend Configuration
DATABASE_URL=sqlite+aiosqlite:///./data.db
SECRET_KEY=change-this-to-a-random-secret-key-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Server Configuration
HOST=0.0.0.0
PORT=8000

# Update System Configuration
UPDATE_CHECK_ENABLED=true
UPDATE_CHECK_URL=https://api.github.com/repos/yourusername/crm-backend/releases/latest
UPDATE_CHECK_INTERVAL=86400
EOF

# Create systemd service file
echo -e "${YELLOW}[i]${NC} Creating systemd service..."
cat > "$PACKAGE_DIR/etc/systemd/system/crm-backend.service" << 'EOF'
[Unit]
Description=CRM Backend Service
After=network.target

[Service]
Type=simple
User=crm-backend
Group=crm-backend
WorkingDirectory=/opt/crm-backend
Environment="PATH=/opt/crm-backend/.venv/bin"
ExecStart=/opt/crm-backend/.venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Create control file
echo -e "${YELLOW}[i]${NC} Creating control file..."
cat > "$PACKAGE_DIR/DEBIAN/control" << EOF
Package: $APP_NAME
Version: $VERSION
Section: web
Priority: optional
Architecture: $ARCHITECTURE
Depends: python3 (>= 3.10), python3-pip, python3-venv, python3-dev, build-essential, libssl-dev, libffi-dev, sqlite3
Maintainer: $MAINTAINER
Description: $DESCRIPTION
 A complete CRM backend system with task management, project tracking,
 contact management, and real-time collaboration features.
 .
 This package includes the FastAPI application, database models,
 and systemd service configuration for easy deployment.
EOF

# Create postinst script (runs after installation)
echo -e "${YELLOW}[i]${NC} Creating post-installation script..."
cat > "$PACKAGE_DIR/DEBIAN/postinst" << 'EOF'
#!/bin/bash
set -e

APP_DIR="/opt/crm-backend"
APP_USER="crm-backend"

echo "Configuring CRM Backend..."

# Create system user if it doesn't exist
if ! id "$APP_USER" &>/dev/null; then
    echo "Creating system user: $APP_USER"
    useradd --system --home-dir "$APP_DIR" --shell /bin/bash "$APP_USER"
fi

# Set ownership
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# Create virtual environment
echo "Creating Python virtual environment..."
cd "$APP_DIR"
if [ ! -d ".venv" ]; then
    sudo -u "$APP_USER" python3 -m venv .venv
    sudo -u "$APP_USER" .venv/bin/pip install --upgrade pip
    sudo -u "$APP_USER" .venv/bin/pip install -r requirements.txt
fi

# Create .env file if it doesn't exist
if [ ! -f "$APP_DIR/.env" ]; then
    echo "Creating .env configuration file..."
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    
    # Generate random secret key
    SECRET_KEY=$(openssl rand -hex 32)
    sed -i "s/change-this-to-a-random-secret-key-in-production/$SECRET_KEY/" "$APP_DIR/.env"
fi

# Initialize database
if [ ! -f "$APP_DIR/data.db" ]; then
    echo "Initializing database..."
    cd "$APP_DIR"
    sudo -u "$APP_USER" .venv/bin/python3 -c "
import asyncio
import sys
sys.path.insert(0, '.')
from app.core.database import init_models
asyncio.run(init_models())
print('Database initialized successfully')
" || echo "Database initialization skipped (may already exist)"
fi

# Reload systemd and enable service
echo "Enabling systemd service..."
systemctl daemon-reload
systemctl enable crm-backend.service

echo ""
echo "========================================="
echo "   CRM Backend Installation Complete!"
echo "========================================="
echo ""
echo "Configuration file: /opt/crm-backend/.env"
echo "Database location:  /opt/crm-backend/data.db"
echo ""
echo "To start the service:"
echo "  sudo systemctl start crm-backend"
echo ""
echo "To view logs:"
echo "  sudo journalctl -u crm-backend -f"
echo ""
echo "Access the application at: http://localhost:8000"
echo ""

exit 0
EOF

# Create prerm script (runs before removal)
echo -e "${YELLOW}[i]${NC} Creating pre-removal script..."
cat > "$PACKAGE_DIR/DEBIAN/prerm" << 'EOF'
#!/bin/bash
set -e

# Stop service before removal
if systemctl is-active --quiet crm-backend; then
    echo "Stopping CRM Backend service..."
    systemctl stop crm-backend
fi

if systemctl is-enabled --quiet crm-backend; then
    echo "Disabling CRM Backend service..."
    systemctl disable crm-backend
fi

exit 0
EOF

# Create postrm script (runs after removal)
echo -e "${YELLOW}[i]${NC} Creating post-removal script..."
cat > "$PACKAGE_DIR/DEBIAN/postrm" << 'EOF'
#!/bin/bash
set -e

if [ "$1" = "purge" ]; then
    echo "Purging CRM Backend data..."
    
    # Remove application directory
    if [ -d "/opt/crm-backend" ]; then
        echo "Removing /opt/crm-backend"
        rm -rf /opt/crm-backend
    fi
    
    # Remove system user
    if id "crm-backend" &>/dev/null; then
        echo "Removing system user: crm-backend"
        userdel crm-backend 2>/dev/null || true
    fi
    
    echo "CRM Backend has been completely removed"
fi

# Reload systemd
systemctl daemon-reload

exit 0
EOF

# Make scripts executable
chmod 755 "$PACKAGE_DIR/DEBIAN/postinst"
chmod 755 "$PACKAGE_DIR/DEBIAN/prerm"
chmod 755 "$PACKAGE_DIR/DEBIAN/postrm"

# Create README
echo -e "${YELLOW}[i]${NC} Creating documentation..."
cat > "$PACKAGE_DIR/opt/$APP_NAME/README.Debian" << 'EOF'
CRM Backend - Debian Package
============================

Installation
------------
This package has been installed to /opt/crm-backend

Configuration
-------------
Edit the configuration file:
  sudo nano /opt/crm-backend/.env

Service Management
------------------
Start:   sudo systemctl start crm-backend
Stop:    sudo systemctl stop crm-backend
Restart: sudo systemctl restart crm-backend
Status:  sudo systemctl status crm-backend

Logs
----
View logs:
  sudo journalctl -u crm-backend -f

Access
------
The application will be available at:
  http://localhost:8000
  http://YOUR-SERVER-IP:8000

Database
--------
SQLite database location:
  /opt/crm-backend/data.db

Backups are stored in:
  /opt/crm-backend/backups/

Uninstallation
--------------
Remove package (keep data):
  sudo apt remove crm-backend

Remove package and data:
  sudo apt purge crm-backend
EOF

# Build the package
echo -e "${YELLOW}[i]${NC} Building Debian package..."
dpkg-deb --build "$PACKAGE_DIR"

# Move package to root
mv "$BUILD_DIR/$PACKAGE_NAME.deb" .

# Calculate package size
SIZE=$(du -h "${PACKAGE_NAME}.deb" | cut -f1)

echo -e "\n${GREEN}=========================================${NC}"
echo -e "${GREEN}   Package Created Successfully!${NC}"
echo -e "${GREEN}=========================================${NC}\n"

echo -e "${BLUE}Package:${NC} ${PACKAGE_NAME}.deb"
echo -e "${BLUE}Size:${NC}    $SIZE"
echo -e "${BLUE}Version:${NC} $VERSION\n"

echo -e "${YELLOW}To install on Ubuntu/Debian:${NC}"
echo -e "  sudo dpkg -i ${PACKAGE_NAME}.deb"
echo -e "  sudo apt-get install -f  ${YELLOW}# Install dependencies if needed${NC}\n"

echo -e "${YELLOW}To start the service:${NC}"
echo -e "  sudo systemctl start crm-backend\n"

echo -e "${YELLOW}To check status:${NC}"
echo -e "  sudo systemctl status crm-backend\n"

# Cleanup
echo -e "${YELLOW}[i]${NC} Cleaning up build directory..."
rm -rf "$BUILD_DIR"

echo -e "${GREEN}[✓]${NC} Done!\n"
