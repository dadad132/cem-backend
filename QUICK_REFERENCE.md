# CRM Backend - Quick Reference

## 🚀 Management Commands

| Command | Description |
|---------|-------------|
| `sudo crm-start` | Start the application |
| `sudo crm-stop` | Stop safely (graceful shutdown, saves data) |
| `sudo crm-restart` | Restart the application |
| `sudo crm-status` | Check application status |
| `sudo crm-logs` | View live logs (Ctrl+C to exit) |
| `sudo crm-backup` | Create manual database backup |

## 📦 Deployment Quick Start

### 1. Create Package (Windows)
```cmd
cd deployment
create_package.bat
```

### 2. Transfer to Server
```bash
scp deployment/packages/crm-backend-deploy_*.tar.gz user@server:/tmp/
```

### 3. Install on Server
```bash
ssh user@server
cd /tmp
tar -xzf crm-backend-deploy_*.tar.gz
cd crm-backend-deploy
sudo bash deployment/install.sh
```

### 4. Start & Access
```bash
sudo crm-start
# Access at: http://YOUR_SERVER_IP:8000
```

## 🔐 First-Time Setup

1. Navigate to `http://YOUR_SERVER_IP:8000`
2. Click **"Sign Up"**
3. Create admin account
4. Complete profile
5. Start using the system!

**Note**: Each server starts with NO users. First user becomes workspace admin.

## 🔧 Common Tasks

### Check Service Status
```bash
sudo systemctl status crm-backend
```

### View Last 50 Log Lines
```bash
sudo journalctl -u crm-backend -n 50
```

### Restart After Code Changes
```bash
sudo crm-stop
# Update code files
sudo crm-start
```

### Restore from Backup
```bash
sudo crm-stop
cd /opt/crm-backend
sudo cp backups/data_backup_MANUAL_*.db data.db
sudo chown crm:crm data.db
sudo crm-start
```

### Check Disk Space
```bash
df -h /opt/crm-backend
du -sh /opt/crm-backend/*
```

## 🛡️ Security Checklist

- [ ] Change default SSH port
- [ ] Use SSH key authentication
- [ ] Set up nginx reverse proxy
- [ ] Enable SSL/TLS with certbot
- [ ] Enable fail2ban
- [ ] Regular security updates
- [ ] Monitor logs regularly
- [ ] Test backup restoration

## 📂 Important Paths

| Path | Description |
|------|-------------|
| `/opt/crm-backend/` | Application directory |
| `/opt/crm-backend/data.db` | SQLite database |
| `/opt/crm-backend/backups/` | Database backups |
| `/opt/crm-backend/logs/` | Application logs |
| `/etc/systemd/system/crm-backend.service` | Systemd service file |

## 🆘 Emergency Recovery

### Database Corrupted
```bash
sudo crm-stop
cd /opt/crm-backend
sudo cp backups/data_backup_MANUAL_*.db data.db
sudo chown crm:crm data.db
sudo crm-start
```

### Service Won't Start
```bash
# Check logs
sudo journalctl -u crm-backend -n 100

# Verify permissions
cd /opt/crm-backend
sudo chown -R crm:crm .

# Check port availability
sudo netstat -tulpn | grep 8000
```

### Out of Disk Space
```bash
# Check space
df -h

# Clean old logs
sudo journalctl --vacuum-time=7d

# Clean old automatic backups (keep manual ones)
cd /opt/crm-backend/backups
sudo find . -name "data_backup_AUTO_*" -mtime +30 -delete
```

## 📊 Monitoring

### CPU & Memory Usage
```bash
top -u crm
htop -u crm
```

### Database Size
```bash
ls -lh /opt/crm-backend/data.db
```

### Active Connections
```bash
sudo netstat -tn | grep :8000 | wc -l
```

### Uptime
```bash
sudo systemctl status crm-backend | grep Active
```

## 🔄 Backup Schedule

- **Automatic**: Every 12 hours (keeps last 10)
- **Manual**: On-demand with `sudo crm-backup` (kept forever)
- **Shutdown**: Automatically before stopping service

## ⚡ Performance Tips

1. **Use SSD** for database storage
2. **Increase RAM** if serving many users
3. **Enable nginx caching** for static files
4. **Set up database indexes** for large datasets
5. **Monitor disk space** regularly
6. **Regular backups** to external storage

---

**For full documentation, see**: `DEPLOYMENT_GUIDE.md`
