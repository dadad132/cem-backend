# CRM Backend - Ubuntu Installer Guide

## Overview

This installer script will automatically set up your CRM backend on Ubuntu with all dependencies, configuration, and systemd service integration.

## Prerequisites

- Ubuntu 20.04 LTS or newer
- Internet connection
- Non-root user with sudo privileges

## Installation Steps

### 1. Transfer Files to Ubuntu Server

You can transfer the entire project folder to your Ubuntu server using one of these methods:

#### Method A: Using SCP (from Windows)
```bash
# From Windows PowerShell or Command Prompt
scp -r C:\Users\admin\Documents\python\crm-backend username@ubuntu-server-ip:/home/username/
```

#### Method B: Using Git
```bash
# On Ubuntu server
git clone https://your-repository-url.git
cd crm-backend
```

#### Method C: Using USB/Direct Copy
1. Copy the entire `crm-backend` folder to a USB drive
2. Insert USB into Ubuntu server
3. Copy folder to your home directory

### 2. Make Installer Executable

```bash
cd ~/crm-backend
chmod +x install_ubuntu.sh
```

### 3. Run the Installer

```bash
./install_ubuntu.sh
```

The installer will:
- Update system packages
- Install Python 3 and dependencies
- Create a virtual environment
- Install all Python packages
- Set up the database
- Create a systemd service
- Configure the firewall
- Start the server automatically

### 4. Access Your Application

After installation completes, the server will be running at:
- **Local**: http://localhost:8000
- **Network**: http://YOUR-SERVER-IP:8000

## Post-Installation

### Managing the Service

```bash
# Check service status
sudo systemctl status crm-backend

# Start the service
sudo systemctl start crm-backend

# Stop the service
sudo systemctl stop crm-backend

# Restart the service
sudo systemctl restart crm-backend

# View logs
sudo journalctl -u crm-backend -f
```

### Configuration

Edit the `.env` file to customize your settings:

```bash
cd ~/crm-backend
nano .env
```

After editing, restart the service:
```bash
sudo systemctl restart crm-backend
```

### Updating the Application

```bash
cd ~/crm-backend
git pull  # If using git
sudo systemctl restart crm-backend
```

### Database Management

- **Database Location**: `~/crm-backend/data.db`
- **Backups**: `~/crm-backend/backups/`
- **Logs**: `~/crm-backend/logs/`

### Uninstalling

```bash
# Stop and disable service
sudo systemctl stop crm-backend
sudo systemctl disable crm-backend

# Remove service file
sudo rm /etc/systemd/system/crm-backend.service
sudo systemctl daemon-reload

# Remove application directory
rm -rf ~/crm-backend
```

## Troubleshooting

### Service Won't Start

Check logs for errors:
```bash
sudo journalctl -u crm-backend -n 50
```

### Port Already in Use

Edit the service file to use a different port:
```bash
sudo nano /etc/systemd/system/crm-backend.service
# Change --port 8000 to --port 8080 (or any available port)
sudo systemctl daemon-reload
sudo systemctl restart crm-backend
```

### Permission Issues

Ensure proper ownership:
```bash
cd ~/crm-backend
sudo chown -R $USER:$USER .
chmod -R 755 .
```

### Database Errors

Reset the database:
```bash
cd ~/crm-backend
rm data.db
source .venv/bin/activate
python3 -c "import asyncio; from app.core.database import init_models; asyncio.run(init_models())"
sudo systemctl restart crm-backend
```

## Firewall Configuration

If you need to open additional ports:
```bash
sudo ufw allow 8000/tcp
sudo ufw status
```

## Nginx Reverse Proxy (Optional)

For production deployment with SSL:

```bash
sudo nano /etc/nginx/sites-available/crm-backend
```

Add:
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and restart:
```bash
sudo ln -s /etc/nginx/sites-available/crm-backend /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## Support

For issues or questions, check:
- Application logs: `~/crm-backend/logs/`
- Service logs: `sudo journalctl -u crm-backend -f`
- System logs: `/var/log/syslog`

## Updating Your Installation

The installer includes a comprehensive update system. To update your installation:

### Automatic Update (Recommended)

```bash
cd ~/crm-backend
./auto_update.sh
```

This script will:
- ✅ Create database backup
- ✅ Stop the service
- ✅ Pull latest code
- ✅ Update dependencies
- ✅ Run migrations
- ✅ Restart the service

### Via Web Interface

1. Log in as administrator
2. Navigate to **Admin** → **System Updates**
3. Click **"Check for Updates Now"**
4. Follow the on-screen instructions

### Manual Update

```bash
cd ~/crm-backend
./update_ubuntu.sh
```

For detailed information, see [UPDATE_SYSTEM.md](UPDATE_SYSTEM.md)

## Security Recommendations

1. **Change default credentials** on first login
2. **Update SECRET_KEY** in `.env` file
3. **Configure HTTPS** using Let's Encrypt
4. **Set up regular backups**
5. **Keep system updated**: `sudo apt update && sudo apt upgrade`
6. **Use strong passwords**
7. **Limit firewall access** to necessary ports only
8. **Regularly check for application updates** via Admin panel

## Performance Tuning

For production use, consider:
- Using PostgreSQL instead of SQLite
- Setting up Nginx as reverse proxy
- Configuring SSL/TLS
- Setting up monitoring (e.g., with Prometheus)
- Implementing log rotation
