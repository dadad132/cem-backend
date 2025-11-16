# Ubuntu Server Installation Guide - CRM Backend

## Quick Install (Copy-Paste This)

```bash
# Download and run installation script
wget https://raw.githubusercontent.com/dadad132/cem-backend/main/install_ubuntu.sh
chmod +x install_ubuntu.sh
./install_ubuntu.sh
```

This will automatically:
- ✅ Install Git, Python3, and dependencies
- ✅ Clone the repository from GitHub
- ✅ Set up Python virtual environment
- ✅ Install all Python packages
- ✅ Create .env file
- ✅ Set up systemd service for auto-start
- ✅ Configure firewall

---

## Manual Installation Steps

### 1. Install Git and Python

```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv
```

### 2. Clone Repository

```bash
cd ~
git clone https://github.com/dadad132/cem-backend.git crm-backend
cd crm-backend
```

### 3. Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Configure Environment

```bash
cp .env.example .env
nano .env
```

**Required settings in .env:**
```
SECRET_KEY=your-secret-key-here
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
INCOMING_MAIL_SERVER=imap.gmail.com
INCOMING_MAIL_PORT=993
INCOMING_MAIL_USER=your-email@gmail.com
INCOMING_MAIL_PASSWORD=your-app-password
```

### 6. Create Directories

```bash
mkdir -p logs uploads/comments backups
```

### 7. Initialize Database

```bash
python -c "from app.core.database import init_db; import asyncio; asyncio.run(init_db())"
```

### 8. Configure Firewall

```bash
sudo ufw allow 8000/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```

### 9. Set Up Systemd Service

Create service file:
```bash
sudo nano /etc/systemd/system/crm-backend.service
```

Paste this content (replace `YOUR_USERNAME` with your actual username):
```ini
[Unit]
Description=CRM Backend Service
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/crm-backend
Environment="PATH=/home/YOUR_USERNAME/crm-backend/.venv/bin"
ExecStart=/home/YOUR_USERNAME/crm-backend/.venv/bin/python start_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable crm-backend
sudo systemctl start crm-backend
```

### 10. Verify Installation

```bash
# Check service status
sudo systemctl status crm-backend

# View logs
sudo journalctl -u crm-backend -f

# Test health endpoint
curl http://localhost:8000/health
```

---

## Server Management

### Start/Stop/Restart

```bash
sudo systemctl start crm-backend
sudo systemctl stop crm-backend
sudo systemctl restart crm-backend
sudo systemctl status crm-backend
```

### View Logs

```bash
# Real-time logs
sudo journalctl -u crm-backend -f

# Last 100 lines
sudo journalctl -u crm-backend -n 100

# Application logs
tail -f ~/crm-backend/logs/*.log
```

### Update from GitHub

```bash
cd ~/crm-backend
sudo systemctl stop crm-backend
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl start crm-backend
```

---

## Troubleshooting

### Service Won't Start

```bash
# Check logs
sudo journalctl -u crm-backend -n 50

# Check if port is already in use
sudo netstat -tulpn | grep 8000

# Try running manually to see errors
cd ~/crm-backend
source .venv/bin/activate
python start_server.py
```

### Database Errors

```bash
# Recreate database
cd ~/crm-backend
source .venv/bin/activate
rm data.db
python -c "from app.core.database import init_db; import asyncio; asyncio.run(init_db())"
```

### Permission Errors

```bash
# Fix file permissions
cd ~/crm-backend
chmod 644 data.db
chmod -R 755 logs uploads backups
```

### Can't Access from Internet

```bash
# Check firewall
sudo ufw status

# Allow port 8000
sudo ufw allow 8000/tcp

# Check if service is running
sudo systemctl status crm-backend

# Check what's listening on port 8000
sudo netstat -tulpn | grep 8000
```

---

## Updating

### Pull Latest Changes

```bash
cd ~/crm-backend
sudo systemctl stop crm-backend
git fetch origin
git reset --hard origin/main
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl start crm-backend
```

### Using Force Update Script

```bash
cd ~/crm-backend
wget https://raw.githubusercontent.com/dadad132/cem-backend/main/force_update.sh
chmod +x force_update.sh
./force_update.sh
sudo systemctl restart crm-backend
```

---

## Security Recommendations

### 1. Change Default Port (Optional)

Edit `.env`:
```
PORT=8080
```

Update firewall:
```bash
sudo ufw allow 8080/tcp
sudo ufw delete allow 8000/tcp
```

### 2. Set Up SSL/HTTPS (Recommended)

Install Nginx as reverse proxy:
```bash
sudo apt install nginx certbot python3-certbot-nginx

# Configure Nginx
sudo nano /etc/nginx/sites-available/crm-backend
```

Basic Nginx config:
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

Enable and get SSL:
```bash
sudo ln -s /etc/nginx/sites-available/crm-backend /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
sudo certbot --nginx -d your-domain.com
```

### 3. Secure SSH

```bash
# Disable root login
sudo nano /etc/ssh/sshd_config
# Set: PermitRootLogin no

# Restart SSH
sudo systemctl restart sshd
```

---

## Backup and Restore

### Manual Backup

```bash
cd ~/crm-backend
mkdir -p backups
cp data.db backups/data.db.$(date +%Y%m%d_%H%M%S)
tar -czf backups/uploads.$(date +%Y%m%d_%H%M%S).tar.gz uploads/
```

### Automatic Backups with Cron

```bash
crontab -e
```

Add this line (daily backup at 2 AM):
```
0 2 * * * cd ~/crm-backend && cp data.db backups/data.db.$(date +\%Y\%m\%d) && find backups/ -name "data.db.*" -mtime +30 -delete
```

### Restore Backup

```bash
cd ~/crm-backend
sudo systemctl stop crm-backend
cp backups/data.db.TIMESTAMP data.db
sudo systemctl start crm-backend
```

---

## Quick Reference

| Task | Command |
|------|---------|
| Start server | `sudo systemctl start crm-backend` |
| Stop server | `sudo systemctl stop crm-backend` |
| Restart server | `sudo systemctl restart crm-backend` |
| Check status | `sudo systemctl status crm-backend` |
| View logs | `sudo journalctl -u crm-backend -f` |
| Update code | `cd ~/crm-backend && git pull` |
| Test health | `curl http://localhost:8000/health` |
| Edit config | `nano ~/crm-backend/.env` |

---

## Support

**Repository:** https://github.com/dadad132/cem-backend

**Health check:** `http://YOUR_SERVER_IP:8000/health`

**Admin panel:** `http://YOUR_SERVER_IP:8000/admin`
