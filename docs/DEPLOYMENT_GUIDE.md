# CRM Backend - Deployment Guide

## Overview
This guide explains how to deploy the CRM Backend application to Ubuntu servers with safe shutdown/startup capabilities and clean installation on every new server.

## Key Features
- ✅ **Graceful Shutdown**: Automatically backs up database before stopping
- ✅ **Fresh Installation**: Each server starts with NO pre-existing users
- ✅ **Easy Management**: Simple commands to start/stop/restart
- ✅ **Systemd Integration**: Runs as a system service
- ✅ **Automatic Backups**: Database backed up every 12 hours
- ✅ **Safe Data Handling**: All data preserved during restarts

---

## System Requirements

### Minimum Requirements
- **OS**: Ubuntu 20.04 LTS or newer
- **RAM**: 2GB minimum (4GB recommended)
- **Disk**: 10GB free space
- **CPU**: 1 core minimum (2+ cores recommended)
- **Python**: 3.8 or newer
- **Access**: Root/sudo privileges

### Network Requirements
- Port 8000 (application)
- Port 80/443 (optional, for nginx reverse proxy)
- Port 22 (SSH access)

---

## Creating Deployment Package

### From Windows (Current System)

1. **Install 7-Zip** (if not already installed):
   - Download from: https://www.7-zip.org/
   - Install and ensure it's in PATH

2. **Create Package**:
   ```cmd
   cd C:\Users\admin\Documents\JP\Python\crm-backend\deployment
   create_package.bat
   ```

3. **Package Location**:
   ```
   deployment/packages/crm-backend-deploy_YYYYMMDD_HHMMSS.tar.gz
   ```

### From Linux/WSL

```bash
cd deployment
bash create_package.sh
```

---

## Deployment Process

### Step 1: Transfer Package to Server

Using SCP:
```bash
scp deployment/packages/crm-backend-deploy_*.tar.gz user@your-server:/tmp/
```

Using SFTP or other file transfer tool of your choice.

### Step 2: Extract on Server

```bash
# SSH into your server
ssh user@your-server

# Navigate to temp directory
cd /tmp

# Extract package
tar -xzf crm-backend-deploy_*.tar.gz
cd crm-backend-deploy
```

### Step 3: Run Installation

```bash
# Run installer with sudo
sudo bash deployment/install.sh
```

The installer will:
1. ✅ Update system packages
2. ✅ Install Python, nginx, and dependencies
3. ✅ Create application user (`crm`)
4. ✅ Set up directory structure
5. ✅ Create Python virtual environment
6. ✅ Install Python packages
7. ✅ Configure firewall (UFW)
8. ✅ Install systemd service
9. ✅ Create management commands

### Step 4: Start Application

```bash
sudo crm-start
```

### Step 5: Access Application

Open browser to:
```
http://YOUR_SERVER_IP:8000
```

### Step 6: Create First User

1. Click "Sign Up" on the login page
2. Create your admin account
3. Complete profile setup
4. You're ready to use the system!

---

## Management Commands

After installation, these commands are available system-wide:

### Start Application
```bash
sudo crm-start
```
Starts the CRM backend service and displays status.

### Stop Application (Graceful)
```bash
sudo crm-stop
```
**IMPORTANT**: This performs a graceful shutdown:
- Creates final database backup
- Closes all connections properly
- Ensures no data loss
- Safe to use anytime

### Restart Application
```bash
sudo crm-restart
```
Restarts the service (uses graceful shutdown).

### Check Status
```bash
sudo crm-status
```
Shows service status and recent logs.

### View Live Logs
```bash
sudo crm-logs
```
Streams live logs (Ctrl+C to exit).

### Create Manual Backup
```bash
sudo crm-backup
```
Creates an instant database backup.

---

## Directory Structure

```
/opt/crm-backend/
├── app/                  # Application code
├── backups/              # Database backups
├── uploads/              # User uploaded files
├── logs/                 # Application logs
├── venv/                 # Python virtual environment
├── data.db               # SQLite database
├── start_server.py       # Server startup script
└── requirements.txt      # Python dependencies
```

---

## Systemd Service

The application runs as a systemd service:

### Service File Location
```
/etc/systemd/system/crm-backend.service
```

### Manual Service Control
```bash
# Start
sudo systemctl start crm-backend

# Stop (graceful)
sudo systemctl stop crm-backend

# Restart
sudo systemctl restart crm-backend

# Status
sudo systemctl status crm-backend

# Enable auto-start on boot
sudo systemctl enable crm-backend

# Disable auto-start
sudo systemctl disable crm-backend
```

### View Logs
```bash
# Live logs
sudo journalctl -u crm-backend -f

# Last 100 lines
sudo journalctl -u crm-backend -n 100

# Since specific time
sudo journalctl -u crm-backend --since "2025-10-31 10:00:00"
```

---

## Database Backups

### Automatic Backups
- **Frequency**: Every 12 hours
- **Location**: `/opt/crm-backend/backups/`
- **Naming**: `data_backup_AUTO_YYYYMMDD_HHMMSS.db`
- **Retention**: Last 10 automatic backups kept

### Manual Backups
- **Command**: `sudo crm-backup`
- **Naming**: `data_backup_MANUAL_YYYYMMDD_HHMMSS.db`
- **Retention**: Never deleted automatically

### Shutdown Backup
- Created automatically on graceful shutdown
- Ensures no data loss between restarts

### Restore from Backup
```bash
cd /opt/crm-backend
sudo systemctl stop crm-backend

# Copy backup to data.db
sudo cp backups/data_backup_MANUAL_20251031_120000.db data.db
sudo chown crm:crm data.db

sudo systemctl start crm-backend
```

---

## Fresh Installation Per Server

### Important: NO Pre-existing Users

Each new server installation:
- ❌ Has NO users in the database
- ❌ Has NO pre-configured accounts
- ✅ Starts completely fresh
- ✅ First user becomes workspace admin

### First User Setup
1. Navigate to application URL
2. Click "Sign Up"
3. Create account (becomes admin automatically)
4. System creates a workspace for you
5. Invite other users from admin panel

---

## Security Considerations

### Firewall Rules
The installer configures UFW with:
- Port 22 (SSH)
- Port 80 (HTTP)
- Port 443 (HTTPS)
- Port 8000 (Application)

### File Permissions
- Application files: `755` (crm user)
- Backups directory: `770` (crm user)
- Database file: Restricted to crm user

### Service Security
- Runs as non-root user (`crm`)
- Protected home directory
- Private temp directory
- System protection enabled

### Recommendations
1. Change default SSH port
2. Use key-based SSH authentication
3. Set up nginx reverse proxy with SSL
4. Enable fail2ban
5. Regular security updates

---

## Troubleshooting

### Service Won't Start
```bash
# Check status
sudo systemctl status crm-backend

# Check logs
sudo journalctl -u crm-backend -n 50

# Check if port is in use
sudo netstat -tulpn | grep 8000

# Verify Python environment
cd /opt/crm-backend
source venv/bin/activate
python --version
```

### Database Issues
```bash
# Check database file
ls -lh /opt/crm-backend/data.db

# Verify database integrity
sqlite3 /opt/crm-backend/data.db "PRAGMA integrity_check;"

# Restore from backup
sudo crm-stop
sudo cp /opt/crm-backend/backups/data_backup_MANUAL_*.db /opt/crm-backend/data.db
sudo chown crm:crm /opt/crm-backend/data.db
sudo crm-start
```

### Permission Issues
```bash
# Reset permissions
cd /opt/crm-backend
sudo chown -R crm:crm .
sudo chmod -R 755 .
sudo chmod -R 770 backups uploads logs
```

### Port Already in Use
```bash
# Find process using port 8000
sudo lsof -i :8000

# Kill process if needed
sudo kill -9 <PID>

# Or change port in start_server.py
```

---

## Upgrading

### To Deploy New Version

1. **Create backup**:
   ```bash
   sudo crm-backup
   ```

2. **Stop service**:
   ```bash
   sudo crm-stop
   ```

3. **Transfer new package**:
   ```bash
   scp new-package.tar.gz user@server:/tmp/
   ```

4. **Extract and replace**:
   ```bash
   cd /tmp
   tar -xzf new-package.tar.gz
   sudo rsync -av --exclude='data.db' --exclude='backups' \
       crm-backend-deploy/ /opt/crm-backend/
   ```

5. **Update dependencies**:
   ```bash
   cd /opt/crm-backend
   source venv/bin/activate
   pip install -r requirements.txt
   ```

6. **Restart service**:
   ```bash
   sudo crm-start
   ```

---

## Uninstallation

To completely remove the application:

```bash
# Stop service
sudo systemctl stop crm-backend
sudo systemctl disable crm-backend

# Remove service file
sudo rm /etc/systemd/system/crm-backend.service
sudo systemctl daemon-reload

# Remove management commands
sudo rm /usr/local/bin/crm-*

# Remove application directory
sudo rm -rf /opt/crm-backend

# Remove user (optional)
sudo userdel crm

# Remove firewall rules (optional)
sudo ufw delete allow 8000/tcp
```

---

## Support & Maintenance

### Log Locations
- **System logs**: `journalctl -u crm-backend`
- **Application logs**: `/opt/crm-backend/logs/`

### Performance Monitoring
```bash
# CPU/Memory usage
top -u crm

# Disk usage
du -sh /opt/crm-backend/*

# Database size
ls -lh /opt/crm-backend/data.db
```

### Regular Maintenance
- Weekly: Check logs for errors
- Weekly: Verify backups exist
- Monthly: Test backup restore
- Monthly: Update system packages
- Quarterly: Review disk space

---

## Production Recommendations

### 1. Use Nginx Reverse Proxy
```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 2. Enable SSL/TLS
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

### 3. Set Up External Backups
```bash
# Cron job to sync backups to remote server
0 2 * * * rsync -av /opt/crm-backend/backups/ user@backup-server:/backups/crm/
```

### 4. Monitor with Systemd
```bash
# Email on service failure
sudo systemctl edit crm-backend.service
# Add: OnFailure=status-email@%n.service
```

---

## Conclusion

This deployment setup provides:
- ✅ Safe shutdown with data preservation
- ✅ Easy start/stop/restart commands  
- ✅ Fresh installation on each server
- ✅ Automatic backups
- ✅ Production-ready systemd service
- ✅ Simple management interface

For questions or issues, check the troubleshooting section or review logs with `sudo crm-logs`.
