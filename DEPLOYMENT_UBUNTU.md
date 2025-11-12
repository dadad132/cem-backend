# Ubuntu Deployment Guide

## Prerequisites
- Ubuntu 20.04 or later
- sudo access
- Internet connection

## Quick Start

### 1. Upload Files to Ubuntu Server
```bash
# Using scp from your local machine:
scp -r crm-backend/ user@your-server:/home/user/

# Or using git:
cd /home/user
git clone <your-repo-url> crm-backend
cd crm-backend
```

### 2. Run Setup Script
```bash
cd crm-backend
chmod +x setup_ubuntu.sh
./setup_ubuntu.sh
```

### 3. Start the Server

**Option A: Run in foreground (for testing)**
```bash
source venv/bin/activate
python start_server.py
```

**Option B: Run in background**
```bash
source venv/bin/activate
nohup python start_server.py > logs/server.log 2>&1 &
```

**Option C: Run as system service (recommended for production)**
```bash
# Edit systemd_service.txt and replace USER and paths
sudo cp systemd_service.txt /etc/systemd/system/crm-backend.service
sudo nano /etc/systemd/system/crm-backend.service  # Edit USER and paths

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable crm-backend
sudo systemctl start crm-backend

# Check status
sudo systemctl status crm-backend
```

## Firewall Configuration

### Allow port 8000
```bash
sudo ufw allow 8000/tcp
sudo ufw reload
```

## Server Access

After starting, the server will be accessible at:
- Local: `http://localhost:8000`
- Network: `http://YOUR_SERVER_IP:8000`

## Managing the Service

### View logs
```bash
# If running as service:
sudo journalctl -u crm-backend -f

# If running with nohup:
tail -f logs/server.log
```

### Stop the server
```bash
# If running as service:
sudo systemctl stop crm-backend

# If running with nohup:
pkill -f start_server.py
```

### Restart the server
```bash
# If running as service:
sudo systemctl restart crm-backend

# If running manually, stop and start again
```

## Database Backups

Backups are automatically created in the `backups/` directory. To manually backup:
```bash
cp data.db backups/manual_backup_$(date +%Y%m%d_%H%M%S).db
```

## Troubleshooting

### Check if Python 3.12 is installed
```bash
python3.12 --version
```

### Check if virtual environment is activated
```bash
which python  # Should show path with 'venv' in it
```

### Check if port 8000 is in use
```bash
sudo lsof -i :8000
```

### Check server process
```bash
ps aux | grep start_server
```

### View error logs
```bash
tail -n 50 logs/error.log
```

## Security Recommendations

1. **Use a reverse proxy** (nginx or apache) in production
2. **Set up SSL/TLS** with Let's Encrypt
3. **Configure firewall** properly
4. **Use strong passwords** for all accounts
5. **Regular backups** - copy backups to another location
6. **Update regularly** - keep system and packages updated

## Nginx Reverse Proxy Example

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Updates and Maintenance

To update the application:
```bash
cd crm-backend
source venv/bin/activate
git pull  # If using git
pip install -r requirements.txt --upgrade
sudo systemctl restart crm-backend  # If using systemd
```
