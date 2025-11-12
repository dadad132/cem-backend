# Ubuntu Installer - Quick Start Guide

## 📦 What You Get

This installer package includes everything needed to deploy the CRM backend on Ubuntu:
- Automatic Python environment setup
- All dependencies installed
- Database initialization
- Systemd service configuration
- Firewall setup
- Auto-start on boot

## 🚀 Quick Installation (3 Steps)

### On Windows (Create Package)

1. **Double-click** `create_installer_package.bat`
   - This creates a deployable package folder/zip
   - Package will be named: `crm-backend-installer_YYYYMMDD_HHMMSS`

### Transfer to Ubuntu

2. **Transfer** the package to your Ubuntu server using:
   
   **Option A - SCP (from Windows Command Prompt):**
   ```cmd
   scp -r crm-backend-installer_* username@server-ip:/home/username/
   ```
   
   **Option B - USB/Network Share:**
   - Copy folder to USB drive
   - Transfer to Ubuntu server
   
   **Option C - Direct Copy:**
   - If you have network access, just copy the folder

### On Ubuntu Server

3. **Install** by running:
   ```bash
   cd crm-backend-installer_*
   chmod +x install_ubuntu.sh
   ./install_ubuntu.sh
   ```

That's it! The server will be running at `http://YOUR-SERVER-IP:8000`

## 📋 What the Installer Does

1. ✅ Updates Ubuntu system packages
2. ✅ Installs Python 3.11+ and dependencies
3. ✅ Creates virtual environment
4. ✅ Installs all Python packages
5. ✅ Sets up SQLite database
6. ✅ Creates systemd service (auto-start)
7. ✅ Configures firewall
8. ✅ Starts the application

**Total time:** 5-10 minutes (depending on internet speed)

## 🎯 After Installation

### Access Your Application
- **Local**: http://localhost:8000
- **Network**: http://YOUR-SERVER-IP:8000
- **Public**: http://YOUR-PUBLIC-IP:8000 (if server has public IP)

### Manage the Service
```bash
# Check status
sudo systemctl status crm-backend

# Stop server
sudo systemctl stop crm-backend

# Start server
sudo systemctl start crm-backend

# Restart server
sudo systemctl restart crm-backend

# View logs
sudo journalctl -u crm-backend -f
```

### Application Files Location
```
~/crm-backend/
├── app/              # Application code
├── .env              # Configuration file
├── data.db           # Database
├── backups/          # Database backups
└── logs/             # Application logs
```

## ⚙️ Configuration

Edit configuration:
```bash
cd ~/crm-backend
nano .env
```

After editing, restart:
```bash
sudo systemctl restart crm-backend
```

## 🔧 Common Tasks

### Change Port
```bash
sudo nano /etc/systemd/system/crm-backend.service
# Change --port 8000 to your desired port
sudo systemctl daemon-reload
sudo systemctl restart crm-backend
```

### Reset Database
```bash
cd ~/crm-backend
rm data.db
source .venv/bin/activate
python3 -c "import asyncio; from app.core.database import init_models; asyncio.run(init_models())"
sudo systemctl restart crm-backend
```

### View Application Logs
```bash
# Real-time logs
sudo journalctl -u crm-backend -f

# Last 100 lines
sudo journalctl -u crm-backend -n 100

# Today's logs
sudo journalctl -u crm-backend --since today
```

### Backup Database
```bash
cd ~/crm-backend
cp data.db backups/data_backup_$(date +%Y%m%d_%H%M%S).db
```

## 🗑️ Uninstall

To completely remove:
```bash
cd ~/crm-backend
chmod +x uninstall_ubuntu.sh
./uninstall_ubuntu.sh
```

Or manually:
```bash
sudo systemctl stop crm-backend
sudo systemctl disable crm-backend
sudo rm /etc/systemd/system/crm-backend.service
sudo systemctl daemon-reload
rm -rf ~/crm-backend
```

## 🔒 Security Tips

After installation:

1. **Change default passwords** on first login
2. **Edit .env file** and set a strong `SECRET_KEY`
3. **Set up HTTPS** using Let's Encrypt (optional)
4. **Configure firewall** to limit access:
   ```bash
   sudo ufw allow 8000/tcp
   sudo ufw enable
   ```
5. **Keep system updated**:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

## 🆘 Troubleshooting

### Server won't start
```bash
# Check logs
sudo journalctl -u crm-backend -n 50

# Check if port is in use
sudo netstat -tulpn | grep 8000

# Try running manually
cd ~/crm-backend
source .venv/bin/activate
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Can't access from other computers
```bash
# Check firewall
sudo ufw status

# Allow port
sudo ufw allow 8000/tcp
```

### Permission errors
```bash
cd ~/crm-backend
sudo chown -R $USER:$USER .
chmod -R 755 .
```

## 📞 Support

- **Service logs**: `sudo journalctl -u crm-backend -f`
- **App logs**: `~/crm-backend/logs/`
- **Configuration**: `~/crm-backend/.env`
- **Database**: `~/crm-backend/data.db`

## 🎉 Success Indicators

After installation, you should see:
- ✅ Green "[✓] Service is running!" message
- ✅ Service status shows "active (running)"
- ✅ Can access http://localhost:8000 in browser
- ✅ Can access http://SERVER-IP:8000 from other devices

---

**Ready to go?** Run `create_installer_package.bat` on Windows, transfer to Ubuntu, and run `./install_ubuntu.sh`!
