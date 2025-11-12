#!/bin/bash

###############################################################################
# CRM Backend - Package Creator
# Creates a deployable package for Ubuntu installation
###############################################################################

echo "========================================="
echo "   Creating Ubuntu Installer Package"
echo "========================================="
echo ""

# Package name
PACKAGE_NAME="crm-backend-installer"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
PACKAGE_DIR="${PACKAGE_NAME}_${TIMESTAMP}"

# Create package directory
echo "[i] Creating package directory..."
mkdir -p "$PACKAGE_DIR"

# Files and directories to include
echo "[i] Copying files..."
cp -r app "$PACKAGE_DIR/"
cp -r alembic "$PACKAGE_DIR/"
cp requirements.txt "$PACKAGE_DIR/"
cp alembic.ini "$PACKAGE_DIR/"
cp .env.example "$PACKAGE_DIR/"
cp install_ubuntu.sh "$PACKAGE_DIR/"
cp uninstall_ubuntu.sh "$PACKAGE_DIR/"
cp INSTALLER_README.md "$PACKAGE_DIR/"
cp README.md "$PACKAGE_DIR/" 2>/dev/null || true

# Create empty directories
mkdir -p "$PACKAGE_DIR/logs"
mkdir -p "$PACKAGE_DIR/backups"
mkdir -p "$PACKAGE_DIR/app/uploads/comments"
echo ".gitkeep" > "$PACKAGE_DIR/logs/.gitkeep"
echo ".gitkeep" > "$PACKAGE_DIR/backups/.gitkeep"

# Make scripts executable
chmod +x "$PACKAGE_DIR/install_ubuntu.sh"
chmod +x "$PACKAGE_DIR/uninstall_ubuntu.sh"

# Create installation instructions
cat > "$PACKAGE_DIR/INSTALL.txt" << 'EOF'
CRM BACKEND - INSTALLATION INSTRUCTIONS
========================================

1. Transfer this entire folder to your Ubuntu server

2. Make the installer executable:
   chmod +x install_ubuntu.sh

3. Run the installer:
   ./install_ubuntu.sh

4. Follow the on-screen instructions

5. Access your application at:
   http://YOUR-SERVER-IP:8000

For detailed instructions, see INSTALLER_README.md

Support: Check logs with 'sudo journalctl -u crm-backend -f'
EOF

# Create archive
echo "[i] Creating archive..."
tar -czf "${PACKAGE_DIR}.tar.gz" "$PACKAGE_DIR"

# Create zip for Windows users
if command -v zip &> /dev/null; then
    zip -r "${PACKAGE_DIR}.zip" "$PACKAGE_DIR" > /dev/null
    echo "[✓] Created ${PACKAGE_DIR}.zip"
fi

# Cleanup
rm -rf "$PACKAGE_DIR"

echo ""
echo "========================================="
echo "   Package Created Successfully!"
echo "========================================="
echo ""
echo "Package file: ${PACKAGE_DIR}.tar.gz"
echo ""
echo "To use:"
echo "  1. Transfer to Ubuntu server"
echo "  2. Extract: tar -xzf ${PACKAGE_DIR}.tar.gz"
echo "  3. cd ${PACKAGE_DIR}"
echo "  4. ./install_ubuntu.sh"
echo ""
