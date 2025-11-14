# Live Server Update Guide

## Quick Update Process

### Method 1: Update with New Package (Recommended)

**On your development machine (Windows):**

1. **Create new installer package:**
   ```powershell
   .\create_installer_package.ps1
   ```
   This creates: `crm-backend-installer_YYYYMMDD_HHMMSS.zip`

2. **Transfer to live server:**
   ```powershell
   # Using SCP (if available)
   scp crm-backend-installer_*.zip user@your-server-ip:~/
   
   # Or use SFTP, FileZilla, WinSCP, etc.
   ```

**On your live Ubuntu server:**

3. **Extract and update:**
   ```bash
   cd ~
   unzip crm-backend-installer_*.zip
   cd crm-backend-installer_*
   
   # Stop the service
   sudo systemctl stop crm-backend
   
   # Backup current installation
   cp -r ~/crm-backend ~/crm-backend.backup.$(date +%Y%m%d_%H%M%S)
   
   # Copy new files (preserve data.db and .env)
   cp -r app ~/crm-backend/
   cp requirements.txt ~/crm-backend/
   cp start_server.py ~/crm-backend/
   cp alembic.ini ~/crm-backend/
   
   # Update dependencies
   cd ~/crm-backend
   source .venv/bin/activate
   pip install -r requirements.txt --upgrade
   
   # Restart service
   sudo systemctl start crm-backend
   sudo systemctl status crm-backend
   ```

---

### Method 2: Update Script (Existing Installation)

If you already have `update_ubuntu.sh` on the server:

```bash
cd ~/crm-backend
./update_ubuntu.sh
```

This will:
- ✅ Stop the service
- ✅ Create database backup
- ✅ Update dependencies
- ✅ Run migrations
- ✅ Restart service

---

### Method 3: Git-Based Update (If Using Git)

**Setup (one-time):**
```bash
cd ~/crm-backend
git init
git remote add origin https://github.com/yourusername/crm-backend.git
```

**To update:**
```bash
cd ~/crm-backend
sudo systemctl stop crm-backend

# Backup database
cp data.db backups/data_backup_$(date +%Y%m%d_%H%M%S).db

# Pull latest code
git pull origin main

# Update dependencies
source .venv/bin/activate
pip install -r requirements.txt --upgrade

# Restart
sudo systemctl start crm-backend
```

---

## Update Checklist

Before updating:
- [ ] **Backup database**: `cp data.db backups/backup_$(date +%Y%m%d).db`
- [ ] **Note current version**: `curl http://localhost:8000/api/system/version`
- [ ] **Check service status**: `sudo systemctl status crm-backend`

During update:
- [ ] **Stop service**: `sudo systemctl stop crm-backend`
- [ ] **Update files**: Copy new app code
- [ ] **Update dependencies**: `pip install -r requirements.txt --upgrade`
- [ ] **Start service**: `sudo systemctl start crm-backend`

After update:
- [ ] **Verify service**: `sudo systemctl status crm-backend`
- [ ] **Check logs**: `sudo journalctl -u crm-backend -n 50`
- [ ] **Test access**: `curl http://localhost:8000/api/system/version`
- [ ] **Test web UI**: Open in browser

---

## Troubleshooting Updates

### Service won't start after update

```bash
# Check logs
sudo journalctl -u crm-backend -n 100

# Check for errors
sudo systemctl status crm-backend

# Try manual start
cd ~/crm-backend
source .venv/bin/activate
python3 start_server.py --local-only
```

### Dependency errors

```bash
cd ~/crm-backend
source .venv/bin/activate

# Reinstall all dependencies
pip install -r requirements.txt --force-reinstall

# Or recreate virtual environment
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Database errors

```bash
# Restore from backup
cd ~/crm-backend
sudo systemctl stop crm-backend
cp backups/data_backup_*.db data.db
sudo systemctl start crm-backend
```

### Roll back to previous version

```bash
# Stop service
sudo systemctl stop crm-backend

# Restore backup
rm -rf ~/crm-backend
mv ~/crm-backend.backup.YYYYMMDD_HHMMSS ~/crm-backend

# Start service
sudo systemctl start crm-backend
```

---

## Zero-Downtime Update (Advanced)

For production systems that need minimal downtime:

1. **Set up blue-green deployment:**
   ```bash
   # Install in different directory
   cd ~
   unzip crm-backend-installer_new.zip
   cd crm-backend-installer_*
   ./install_ubuntu.sh
   # This creates ~/crm-backend-new
   ```

2. **Test new version:**
   ```bash
   cd ~/crm-backend-new
   python3 start_server.py --port 8001
   # Test on port 8001
   ```

3. **Swap versions:**
   ```bash
   sudo systemctl stop crm-backend
   mv ~/crm-backend ~/crm-backend-old
   mv ~/crm-backend-new ~/crm-backend
   sudo systemctl start crm-backend
   ```

---

## Automated Update Script

Create a script `quick_update.sh`:

```bash
#!/bin/bash
set -e

APP_DIR="$HOME/crm-backend"
SERVICE="crm-backend"

echo "=== CRM Backend Quick Update ==="

# Backup
echo "[1/5] Creating backup..."
cd "$APP_DIR"
cp data.db backups/data_backup_$(date +%Y%m%d_%H%M%S).db

# Stop
echo "[2/5] Stopping service..."
sudo systemctl stop $SERVICE

# Update dependencies
echo "[3/5] Updating dependencies..."
source .venv/bin/activate
pip install -r requirements.txt --upgrade -q

# Start
echo "[4/5] Starting service..."
sudo systemctl start $SERVICE

# Verify
echo "[5/5] Verifying..."
sleep 3
if sudo systemctl is-active --quiet $SERVICE; then
    echo "✓ Update successful!"
    curl -s http://localhost:8000/api/system/version | python3 -m json.tool
else
    echo "✗ Service failed to start"
    sudo journalctl -u $SERVICE -n 20
    exit 1
fi
```

Make it executable:
```bash
chmod +x quick_update.sh
```

---

## Update via .deb Package (If Using DEB)

If you built the .deb package:

```bash
# Transfer new .deb to server
scp crm-backend_1.2.0_all.deb user@server:~/

# On server, install new version
sudo dpkg -i crm-backend_1.2.0_all.deb

# Service restarts automatically
sudo systemctl status crm-backend
```

---

## Monitoring After Update

```bash
# Watch logs in real-time
sudo journalctl -u crm-backend -f

# Check resource usage
ps aux | grep uvicorn

# Test endpoints
curl http://localhost:8000/api/system/health
curl http://localhost:8000/api/system/version
```

---

## Update Frequency Recommendations

- **Security patches**: Immediately
- **Bug fixes**: Within 1-7 days
- **New features**: During maintenance window
- **Major updates**: Test on staging first

---

## Backup Strategy

**Before every update:**
```bash
# Manual backup
cd ~/crm-backend
cp data.db backups/data_backup_manual_$(date +%Y%m%d_%H%M%S).db

# Keep last 10 backups
cd backups
ls -t data_backup_*.db | tail -n +11 | xargs rm -f
```

**Automated daily backup** (add to crontab):
```bash
crontab -e

# Add this line (daily at 2 AM)
0 2 * * * cd ~/crm-backend && cp data.db backups/data_backup_auto_$(date +\%Y\%m\%d).db
```

---

## Questions?

- Check service: `sudo systemctl status crm-backend`
- View logs: `sudo journalctl -u crm-backend -f`
- Test API: `curl http://localhost:8000/api/system/version`
- Restore backup: `cp backups/data_backup_*.db data.db`
