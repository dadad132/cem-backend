# 🚀 CRM Backend - Ubuntu Deployment Package

## Overview

This package contains everything you need to deploy the CRM backend on Ubuntu with a single command.

## 📦 Package Contents

```
crm-backend-installer/
├── install_ubuntu.sh          # Main installer script
├── uninstall_ubuntu.sh        # Uninstaller script
├── update_ubuntu.sh           # Update script
├── INSTALL.txt                # Quick installation guide
├── QUICK_INSTALL.md           # Detailed quick start
├── INSTALLER_README.md        # Full documentation
├── app/                       # Application code
├── alembic/                   # Database migrations
├── requirements.txt           # Python dependencies
├── .env.example               # Configuration template
└── README.md                  # This file
```

## ⚡ Quick Start

### 1️⃣ On Windows - Create Package

Double-click: **`create_installer_package.bat`**

This creates a ready-to-deploy package.

### 2️⃣ Transfer to Ubuntu

**Option A - SCP:**
```cmd
scp -r crm-backend-installer_* username@ubuntu-ip:/home/username/
```

**Option B - USB/Manual Copy:**
Copy the folder to your Ubuntu server

### 3️⃣ On Ubuntu - Install

```bash
cd crm-backend-installer_*
chmod +x install_ubuntu.sh
./install_ubuntu.sh
```

**Done!** Access at `http://YOUR-SERVER-IP:8000`

## ✨ Features

- ✅ **Fully Automated** - One command installation
- ✅ **Systemd Service** - Auto-starts on boot
- ✅ **Database Setup** - SQLite configured automatically
- ✅ **Firewall Config** - Port opened automatically
- ✅ **Virtual Environment** - Isolated Python packages
- ✅ **Log Management** - Logs accessible via journalctl
- ✅ **Easy Updates** - Update script included
- ✅ **Clean Uninstall** - Removal script included

## 🎯 What Gets Installed

| Component | Description |
|-----------|-------------|
| Python 3.11+ | Latest Python with pip |
| Virtual Env | Isolated Python environment |
| Dependencies | FastAPI, Uvicorn, SQLAlchemy, etc. |
| Database | SQLite with auto-initialization |
| Systemd Service | Auto-start and management |
| Firewall Rules | Port 8000 opened |
| Application | Full CRM backend |

## 📍 Locations After Install

| Item | Location |
|------|----------|
| Application | `~/crm-backend/` |
| Configuration | `~/crm-backend/.env` |
| Database | `~/crm-backend/data.db` |
| Backups | `~/crm-backend/backups/` |
| Logs | `~/crm-backend/logs/` |
| Service | `/etc/systemd/system/crm-backend.service` |

## 🔧 Management Commands

### Service Control
```bash
sudo systemctl start crm-backend      # Start
sudo systemctl stop crm-backend       # Stop
sudo systemctl restart crm-backend    # Restart
sudo systemctl status crm-backend     # Check status
```

### View Logs
```bash
sudo journalctl -u crm-backend -f     # Real-time
sudo journalctl -u crm-backend -n 100 # Last 100 lines
```

### Configuration
```bash
nano ~/crm-backend/.env               # Edit config
sudo systemctl restart crm-backend    # Apply changes
```

## 🔄 Updates

To update an existing installation:

```bash
cd ~/crm-backend
chmod +x update_ubuntu.sh
./update_ubuntu.sh
```

## 🗑️ Uninstall

To completely remove:

```bash
cd ~/crm-backend
chmod +x uninstall_ubuntu.sh
./uninstall_ubuntu.sh
```

## 🌐 Access URLs

After installation:

- **Localhost**: http://localhost:8000
- **Local Network**: http://YOUR-LOCAL-IP:8000
- **Public**: http://YOUR-PUBLIC-IP:8000 (if available)

The installer displays all available URLs at the end.

## 🔒 Security Checklist

After installation:

- [ ] Change default admin password
- [ ] Edit `~/crm-backend/.env` and set strong `SECRET_KEY`
- [ ] Configure firewall: `sudo ufw enable`
- [ ] Set up HTTPS (optional, using Let's Encrypt)
- [ ] Regular backups: `cp ~/crm-backend/data.db ~/backups/`
- [ ] Keep updated: `sudo apt update && sudo apt upgrade`

## 📊 System Requirements

### Minimum
- Ubuntu 20.04 LTS or newer
- 1 GB RAM
- 1 CPU core
- 5 GB disk space
- Internet connection

### Recommended
- Ubuntu 22.04 LTS
- 2 GB RAM
- 2 CPU cores
- 10 GB disk space
- Stable internet

## 🆘 Troubleshooting

### Installation fails
```bash
# Check internet connection
ping google.com

# Try manual install
sudo apt update
sudo apt install python3 python3-pip python3-venv -y
```

### Service won't start
```bash
# View error logs
sudo journalctl -u crm-backend -n 50

# Try manual start
cd ~/crm-backend
source .venv/bin/activate
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Can't access from browser
```bash
# Check if service is running
sudo systemctl status crm-backend

# Check firewall
sudo ufw status
sudo ufw allow 8000/tcp

# Check port
sudo netstat -tulpn | grep 8000
```

### Port already in use
```bash
# Find what's using the port
sudo lsof -i :8000

# Change port in service
sudo nano /etc/systemd/system/crm-backend.service
# Change --port 8000 to --port 8080
sudo systemctl daemon-reload
sudo systemctl restart crm-backend
```

## 📚 Documentation Files

- **INSTALL.txt** - Basic installation steps
- **QUICK_INSTALL.md** - Quick start guide with examples
- **INSTALLER_README.md** - Complete documentation
- **README.md** - This file

## 🎓 First Time Setup

1. **Install**: Run `install_ubuntu.sh`
2. **Access**: Open http://YOUR-SERVER-IP:8000
3. **Login**: Use default admin credentials (created on first run)
4. **Configure**: Edit settings in web interface
5. **Secure**: Change passwords and update `.env` file

## 🔗 Useful Links

- Service logs: `sudo journalctl -u crm-backend -f`
- Application logs: `~/crm-backend/logs/`
- Configuration: `~/crm-backend/.env`
- Database: `~/crm-backend/data.db`

## ⚙️ Advanced Configuration

### Using PostgreSQL Instead of SQLite

Edit `~/crm-backend/.env`:
```bash
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/dbname
```

Install PostgreSQL:
```bash
sudo apt install postgresql postgresql-contrib
pip install asyncpg
sudo systemctl restart crm-backend
```

### Setting Up Nginx Reverse Proxy

```bash
sudo apt install nginx
sudo nano /etc/nginx/sites-available/crm-backend
```

Add configuration:
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

Enable:
```bash
sudo ln -s /etc/nginx/sites-available/crm-backend /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### SSL/HTTPS with Let's Encrypt

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## 📝 Support & Help

If you encounter issues:

1. Check logs: `sudo journalctl -u crm-backend -f`
2. Verify service: `sudo systemctl status crm-backend`
3. Check connectivity: `curl http://localhost:8000`
4. Review configuration: `cat ~/crm-backend/.env`

## 🎉 Success!

You should see:
- ✅ Service running: `sudo systemctl status crm-backend`
- ✅ Accessible in browser: http://YOUR-IP:8000
- ✅ Green checkmarks from installer
- ✅ No errors in logs

---

**Questions?** Check the detailed guides in `INSTALLER_README.md` or `QUICK_INSTALL.md`

**Ready to install?** Run `./install_ubuntu.sh` and follow the prompts!
