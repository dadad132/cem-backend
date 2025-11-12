# CRM Backend - Debian Package Installation Guide

## Overview

The `.deb` package provides the easiest way to install CRM Backend on Ubuntu/Debian systems. It includes:
- Automatic dependency installation
- System user creation
- Python virtual environment setup
- Database initialization
- SystemD service configuration
- Auto-start on boot

## Prerequisites

- Ubuntu 20.04 LTS or later / Debian 11 or later
- `sudo` privileges

## Building the Package

### On Ubuntu/Debian (Linux):

```bash
# Make the script executable
chmod +x create_deb_package.sh

# Run the builder
./create_deb_package.sh
```

This will create: `crm-backend_1.2.0_all.deb`

### On Windows (for transfer to Ubuntu):

The package must be built on a Linux system with `dpkg-deb` tools. 

**Option 1: Use WSL (Windows Subsystem for Linux)**
```powershell
# Install WSL if not already installed
wsl --install

# Transfer files to WSL
wsl
cd /mnt/c/Users/admin/Documents/python/crm-backend
chmod +x create_deb_package.sh
./create_deb_package.sh
```

**Option 2: Transfer to Ubuntu and build there**
1. Transfer the entire project folder to Ubuntu
2. Run `./create_deb_package.sh` on Ubuntu

## Installation

### 1. Install the Package

```bash
# Install the .deb package
sudo dpkg -i crm-backend_1.2.0_all.deb

# If there are missing dependencies, install them
sudo apt-get install -f
```

The installer will automatically:
- Create system user `crm-backend`
- Install to `/opt/crm-backend`
- Create Python virtual environment
- Install all Python dependencies
- Initialize the database
- Generate secure secret key
- Enable systemd service

### 2. Start the Service

```bash
# Start the service
sudo systemctl start crm-backend

# Enable auto-start on boot (already done by installer)
sudo systemctl enable crm-backend

# Check status
sudo systemctl status crm-backend
```

### 3. Access the Application

The application will be available at:
- Local: `http://localhost:8000`
- Network: `http://YOUR-SERVER-IP:8000`

## Configuration

### Edit Configuration

```bash
sudo nano /opt/crm-backend/.env
```

### Important Settings

```env
# Change the secret key (auto-generated)
SECRET_KEY=your-secret-key

# Email configuration (if using email features)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password

# Update check URL (optional)
UPDATE_CHECK_URL=https://api.github.com/repos/yourusername/crm-backend/releases/latest
```

After editing, restart the service:
```bash
sudo systemctl restart crm-backend
```

## Service Management

### Start/Stop/Restart

```bash
# Start
sudo systemctl start crm-backend

# Stop
sudo systemctl stop crm-backend

# Restart
sudo systemctl restart crm-backend

# Status
sudo systemctl status crm-backend
```

### View Logs

```bash
# Follow logs in real-time
sudo journalctl -u crm-backend -f

# View recent logs
sudo journalctl -u crm-backend -n 100

# View logs since boot
sudo journalctl -u crm-backend -b
```

### Auto-start on Boot

```bash
# Enable (already done by installer)
sudo systemctl enable crm-backend

# Disable
sudo systemctl disable crm-backend
```

## File Locations

| Item | Location |
|------|----------|
| Application | `/opt/crm-backend/` |
| Configuration | `/opt/crm-backend/.env` |
| Database | `/opt/crm-backend/data.db` |
| Backups | `/opt/crm-backend/backups/` |
| Logs (app) | `/opt/crm-backend/logs/` |
| Logs (system) | `journalctl -u crm-backend` |
| Service file | `/etc/systemd/system/crm-backend.service` |
| Virtual environment | `/opt/crm-backend/.venv/` |

## Database Management

### Manual Backup

```bash
cd /opt/crm-backend
sudo -u crm-backend .venv/bin/python3 -c "from app.core.backup import backup_manager; backup_manager.create_backup(is_manual=True)"
```

### View Database

```bash
cd /opt/crm-backend
sudo -u crm-backend sqlite3 data.db
```

### Restore from Backup

```bash
cd /opt/crm-backend
sudo systemctl stop crm-backend
sudo -u crm-backend cp backups/data_backup_MANUAL_*.db data.db
sudo systemctl start crm-backend
```

## Updates

### Manual Update

1. Stop the service
   ```bash
   sudo systemctl stop crm-backend
   ```

2. Install new .deb package
   ```bash
   sudo dpkg -i crm-backend_NEW_VERSION_all.deb
   ```

3. Start the service
   ```bash
   sudo systemctl start crm-backend
   ```

### Check Version

```bash
curl http://localhost:8000/api/system/version
```

## Firewall Configuration

### Allow Port 8000

```bash
# UFW
sudo ufw allow 8000/tcp

# iptables
sudo iptables -A INPUT -p tcp --dport 8000 -j ACCEPT
sudo iptables-save
```

## Troubleshooting

### Service won't start

```bash
# Check status
sudo systemctl status crm-backend

# Check logs
sudo journalctl -u crm-backend -n 50

# Check file permissions
ls -la /opt/crm-backend/

# Verify user exists
id crm-backend
```

### Can't access from network

```bash
# Check if service is listening
sudo netstat -tlnp | grep 8000

# Check firewall
sudo ufw status

# Verify configuration
cat /opt/crm-backend/.env | grep HOST
```

### Database errors

```bash
# Check database file
ls -l /opt/crm-backend/data.db

# Verify permissions
sudo chown crm-backend:crm-backend /opt/crm-backend/data.db

# Reinitialize database (CAUTION: This will delete data!)
sudo systemctl stop crm-backend
cd /opt/crm-backend
sudo -u crm-backend rm data.db
sudo -u crm-backend .venv/bin/python3 -c "import asyncio, sys; sys.path.insert(0, '.'); from app.core.database import init_models; asyncio.run(init_models())"
sudo systemctl start crm-backend
```

## Uninstallation

### Remove Package (Keep Data)

```bash
sudo apt remove crm-backend
```

This removes the application but keeps:
- Database (`/opt/crm-backend/data.db`)
- Configuration (`/opt/crm-backend/.env`)
- Backups (`/opt/crm-backend/backups/`)

### Complete Removal (Delete Everything)

```bash
sudo apt purge crm-backend
```

This removes:
- Application files
- Configuration
- Database
- System user
- All data

**⚠️ Warning: This cannot be undone!**

## Upgrading

### Upgrade to New Version

```bash
# Backup first!
cd /opt/crm-backend
sudo -u crm-backend .venv/bin/python3 -c "from app.core.backup import backup_manager; backup_manager.create_backup(is_manual=True)"

# Install new version
sudo dpkg -i crm-backend_NEW_VERSION_all.deb

# Service will restart automatically
```

## Security Best Practices

1. **Change the secret key** in `.env` file
2. **Use strong passwords** for admin accounts
3. **Configure firewall** to restrict access
4. **Enable HTTPS** using reverse proxy (nginx/Apache)
5. **Regular backups** of database
6. **Keep system updated**: `sudo apt update && sudo apt upgrade`

## Performance Tuning

### For Production

Edit `/etc/systemd/system/crm-backend.service`:

```ini
[Service]
# Add workers (CPU cores * 2 + 1)
ExecStart=/opt/crm-backend/.venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# Increase file limits
LimitNOFILE=65535
```

Then reload:
```bash
sudo systemctl daemon-reload
sudo systemctl restart crm-backend
```

## Support

For issues or questions:
- Check logs: `sudo journalctl -u crm-backend -f`
- Verify configuration: `/opt/crm-backend/.env`
- Check service status: `sudo systemctl status crm-backend`
