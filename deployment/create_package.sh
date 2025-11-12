#!/bin/bash
# Package the CRM application for deployment

set -e

echo "============================================================"
echo "Creating CRM Backend Deployment Package"
echo "============================================================"
echo ""

# Configuration
PACKAGE_NAME="crm-backend-deploy"
VERSION=$(date +%Y%m%d_%H%M%S)
PACKAGE_FILE="${PACKAGE_NAME}_${VERSION}.tar.gz"

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "📦 Package configuration:"
echo "   Name: $PACKAGE_NAME"
echo "   Version: $VERSION"
echo "   Output: $PACKAGE_FILE"
echo ""

# Create temporary packaging directory
TEMP_DIR=$(mktemp -d)
PACKAGE_DIR="$TEMP_DIR/$PACKAGE_NAME"

echo "📁 Creating package structure..."
mkdir -p "$PACKAGE_DIR"

# Copy application files
echo "📋 Copying application files..."
cd "$PROJECT_ROOT"

# Copy everything except excludes
rsync -av \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='.gitignore' \
    --exclude='data.db' \
    --exclude='backups/*' \
    --exclude='uploads/*' \
    --exclude='*.log' \
    --exclude='*.pid' \
    --exclude='node_modules' \
    --exclude='.vscode' \
    --exclude='.idea' \
    --exclude='*.tar.gz' \
    --exclude='deployment/packages/*' \
    ./ "$PACKAGE_DIR/"

echo "✅ Files copied"

# Ensure deployment directory exists in package
mkdir -p "$PACKAGE_DIR/deployment"

# Create package README
cat > "$PACKAGE_DIR/INSTALL_README.txt" <<'EOF'
============================================================
CRM Backend - Installation Instructions
============================================================

SYSTEM REQUIREMENTS:
  - Ubuntu 20.04 LTS or newer
  - 2GB RAM minimum
  - 10GB disk space
  - Python 3.8 or newer
  - Root/sudo access

INSTALLATION STEPS:

1. Extract this package:
   tar -xzf crm-backend-deploy_*.tar.gz
   cd crm-backend-deploy

2. Run the installation script:
   sudo bash deployment/install.sh

3. Start the application:
   sudo crm-start

4. Access the application:
   Open your browser to: http://YOUR_SERVER_IP:8000

5. Create your first admin user through the web interface

MANAGEMENT COMMANDS:
  crm-start    - Start the application
  crm-stop     - Stop the application (graceful, saves data)
  crm-restart  - Restart the application
  crm-status   - Check application status
  crm-logs     - View live application logs
  crm-backup   - Create manual database backup

IMPORTANT NOTES:
  - Each server starts fresh with NO pre-existing users
  - First user created through signup becomes workspace admin
  - Database is automatically backed up every 12 hours
  - Backups are stored in /opt/crm-backend/backups
  - Use 'crm-stop' for safe shutdown (preserves all data)

TROUBLESHOOTING:
  - Check status: sudo crm-status
  - View logs: sudo crm-logs
  - Check service: sudo systemctl status crm-backend
  - Restart: sudo crm-restart

For support, contact your system administrator.
============================================================
EOF

# Create version info file
cat > "$PACKAGE_DIR/VERSION.txt" <<EOF
CRM Backend Deployment Package
Version: $VERSION
Build Date: $(date)
Package: $PACKAGE_FILE
EOF

echo "📝 Documentation created"

# Create the tarball
echo "📦 Creating deployment package..."
cd "$TEMP_DIR"
tar -czf "$PROJECT_ROOT/deployment/packages/$PACKAGE_FILE" "$PACKAGE_NAME"

# Cleanup
rm -rf "$TEMP_DIR"

# Create packages directory if it doesn't exist
mkdir -p "$PROJECT_ROOT/deployment/packages"
mv "$PROJECT_ROOT/deployment/packages/$PACKAGE_FILE" "$PROJECT_ROOT/deployment/packages/" 2>/dev/null || true

# Get package size
PACKAGE_PATH="$PROJECT_ROOT/deployment/packages/$PACKAGE_FILE"
PACKAGE_SIZE=$(du -h "$PACKAGE_PATH" | cut -f1)

echo ""
echo "============================================================"
echo "✅ Deployment Package Created Successfully!"
echo "============================================================"
echo ""
echo "📦 Package Details:"
echo "   File: $PACKAGE_FILE"
echo "   Location: deployment/packages/$PACKAGE_FILE"
echo "   Size: $PACKAGE_SIZE"
echo ""
echo "🚀 Deployment Instructions:"
echo ""
echo "1. Copy package to your Ubuntu server:"
echo "   scp deployment/packages/$PACKAGE_FILE user@server:/tmp/"
echo ""
echo "2. On the server, extract and install:"
echo "   cd /tmp"
echo "   tar -xzf $PACKAGE_FILE"
echo "   cd $PACKAGE_NAME"
echo "   sudo bash deployment/install.sh"
echo ""
echo "3. Start the application:"
echo "   sudo crm-start"
echo ""
echo "4. Access at: http://YOUR_SERVER_IP:8000"
echo ""
echo "============================================================"
