# Update System - Quick Reference

## 🚀 Quick Commands

### Check for Updates
```bash
# Via web interface
http://your-server:8000/admin/updates

# Via command line
cd ~/crm-backend
./auto_update.sh --check
```

### Install Updates
```bash
# Automatic (recommended)
./auto_update.sh

# Alternative
./update_ubuntu.sh

# Manual
git pull origin main
pip install -r requirements.txt --upgrade
sudo systemctl restart crm-backend
```

### Check Version
```bash
# Command line
python3 -c "from app import VERSION; print(VERSION)"

# Via API
curl http://localhost:8000/api/system/version
```

## 📋 Common Tasks

### Create Installer Package
```powershell
# Windows PowerShell
.\create_installer_package.ps1

# Windows Batch
create_installer_package.bat
```

### Install on Ubuntu
```bash
chmod +x install_ubuntu.sh
./install_ubuntu.sh
```

### Service Management
```bash
# Start
sudo systemctl start crm-backend

# Stop
sudo systemctl stop crm-backend

# Restart
sudo systemctl restart crm-backend

# Status
sudo systemctl status crm-backend

# Logs
sudo journalctl -u crm-backend -f
```

### Backup & Restore
```bash
# Manual backup
cp data.db backups/manual_backup_$(date +%Y%m%d).db

# List backups
ls -lh backups/

# Restore backup
sudo systemctl stop crm-backend
cp backups/backup_file.db data.db
sudo systemctl start crm-backend
```

## 🔧 Configuration

### Environment Variables (.env)
```env
# Update System
UPDATE_CHECK_ENABLED=true
UPDATE_CHECK_URL=https://api.github.com/repos/user/repo/releases/latest
UPDATE_CHECK_INTERVAL=86400

# Server
HOST=0.0.0.0
PORT=8000

# Database
DATABASE_URL=sqlite:///./data.db

# Security
SECRET_KEY=your-secret-key-here
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

## 🌐 API Endpoints

### Public Endpoints
```bash
# Version info
GET /api/system/version

# Health check
GET /api/system/health
```

### Admin Endpoints
```bash
# Check updates (requires admin auth)
GET /api/system/updates/check
```

## 📁 Directory Structure
```
~/crm-backend/
├── app/                    # Application code
├── alembic/                # Migrations
├── logs/                   # Application logs
├── backups/                # Database backups
├── updates/                # Update files
├── data.db                 # SQLite database
├── .env                    # Configuration
├── requirements.txt        # Dependencies
├── auto_update.sh          # Update script
└── update_ubuntu.sh        # Alt update script
```

## 🔍 Troubleshooting

### Service Won't Start
```bash
# Check logs
sudo journalctl -u crm-backend -n 50

# Test manually
cd ~/crm-backend
source .venv/bin/activate
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Check permissions
ls -la data.db
sudo chown $USER:$USER data.db
```

### Update Fails
```bash
# Restore from backup
cd ~/crm-backend
ls -lh backups/
cp backups/data_pre_update_*.db data.db
sudo systemctl start crm-backend
```

### Can't Access Web UI
```bash
# Check if service is running
sudo systemctl status crm-backend

# Check firewall
sudo ufw status
sudo ufw allow 8000/tcp

# Check port
netstat -tulpn | grep 8000
```

### Database Issues
```bash
# Check database file
ls -lh data.db

# Verify database
python3 -c "
from app.core.database import engine
import asyncio
asyncio.run(engine.connect())
print('Database OK')
"
```

## 📊 Monitoring

### Real-time Logs
```bash
# All logs
sudo journalctl -u crm-backend -f

# Last 100 lines
sudo journalctl -u crm-backend -n 100

# Errors only
sudo journalctl -u crm-backend -p err
```

### System Status
```bash
# Service status
systemctl status crm-backend

# Process info
ps aux | grep uvicorn

# Port check
ss -tulpn | grep 8000

# Disk space
df -h
du -sh ~/crm-backend
```

## 🔐 Security

### Change Admin Password
```bash
# Via web interface
http://your-server:8000/auth/profile

# Via command line
python3 -c "
from app.core.security import get_password_hash
print(get_password_hash('new_password'))
"
```

### Firewall Setup
```bash
# Enable firewall
sudo ufw enable

# Allow SSH
sudo ufw allow 22/tcp

# Allow HTTP
sudo ufw allow 8000/tcp

# Check status
sudo ufw status
```

## 📦 Package Info

### Dependencies
```bash
# List installed
pip list

# Check for updates
pip list --outdated

# Install/update
pip install -r requirements.txt --upgrade
```

### Python Version
```bash
# Check version
python3 --version

# Check location
which python3
```

## 🆘 Emergency Procedures

### Complete Reset
```bash
# Backup first!
cp data.db ~/data_backup.db

# Reset database
rm data.db
python3 -c "
import asyncio
from app.core.database import init_models
asyncio.run(init_models())
"

# Restart
sudo systemctl restart crm-backend
```

### Full Reinstall
```bash
# Backup data
cp data.db ~/data_backup.db
cp .env ~/env_backup

# Uninstall
./uninstall_ubuntu.sh

# Reinstall
./install_ubuntu.sh

# Restore data
cp ~/data_backup.db data.db
```

## 📞 Support

- Documentation: `/docs` folder
- Logs: `~/crm-backend/logs/`
- Service logs: `sudo journalctl -u crm-backend -f`
- System logs: `/var/log/syslog`

## ⚡ Performance

### Optimize Database
```bash
# SQLite optimization
sqlite3 data.db "VACUUM;"
sqlite3 data.db "ANALYZE;"
```

### Check Resource Usage
```bash
# CPU and memory
top
htop  # if installed

# Disk I/O
iotop  # if installed

# Network
netstat -tulpn
```

## 🎯 Quick Links

- **Admin Panel**: http://localhost:8000/admin
- **API Docs**: http://localhost:8000/docs
- **Updates**: http://localhost:8000/admin/updates
- **Users**: http://localhost:8000/admin/users
- **Backups**: http://localhost:8000/admin/backups

---

**Version**: 1.2.0  
**Last Updated**: January 2025  
**For**: Ubuntu 20.04+ / Debian 10+
