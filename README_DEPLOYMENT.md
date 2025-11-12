# Ubuntu Deployment - Quick Reference

## 📦 Deployment Files Created

### Essential Files
- `requirements.txt` - Python package dependencies
- `setup_ubuntu.sh` - Automated setup script for Ubuntu
- `start_ubuntu.sh` - Simple startup script
- `deploy.sh` - Quick deployment script
- `DEPLOYMENT_UBUNTU.md` - Complete deployment guide

### Optional Files  
- `systemd_service.txt` - Template for running as system service
- `.deployignore` - Files to exclude when deploying

## 🚀 Quick Start (3 Steps)

### 1. Upload to Ubuntu Server
```bash
# Option A: Using SCP
scp -r crm-backend/ user@your-server-ip:/home/user/

# Option B: Using Git
ssh user@your-server-ip
git clone <your-repo> crm-backend
cd crm-backend
```

### 2. Run Deployment
```bash
chmod +x deploy.sh
./deploy.sh
```

### 3. Start Server
```bash
./start_ubuntu.sh
```

**That's it!** Server will be running on `http://your-server-ip:8000`

---

## 📋 What Each File Does

### `requirements.txt`
Contains all Python packages needed to run the CRM:
- FastAPI (web framework)
- SQLModel (database)
- Authentication libraries
- And more...

### `setup_ubuntu.sh`
Automated setup that:
- ✓ Installs Python 3.12
- ✓ Creates virtual environment
- ✓ Installs all dependencies
- ✓ Creates necessary directories
- ✓ Sets permissions

### `start_ubuntu.sh`
Simple script that:
- ✓ Activates virtual environment
- ✓ Runs `start_server.py`

### `deploy.sh`
One-command deployment:
- ✓ Makes scripts executable
- ✓ Runs complete setup
- ✓ Ready to start

---

## 🔧 Advanced Options

### Run as Background Service
```bash
source venv/bin/activate
nohup python start_server.py > logs/server.log 2>&1 &
```

### Run as System Service (Production)
See `DEPLOYMENT_UBUNTU.md` for complete instructions

### Configure Firewall
```bash
sudo ufw allow 8000/tcp
```

---

## 📖 Full Documentation
See `DEPLOYMENT_UBUNTU.md` for:
- Detailed setup instructions
- Security recommendations
- Nginx reverse proxy setup
- SSL/TLS configuration
- Troubleshooting guide
- Maintenance procedures

---

## ✅ Before Deploying - Checklist

- [ ] Backup your current database (`data.db`)
- [ ] Review `requirements.txt` - all packages needed
- [ ] Note your server IP address
- [ ] Have sudo access on Ubuntu server
- [ ] Server has internet connection
- [ ] Port 8000 is available/allowed

---

## 🆘 Quick Troubleshooting

**Server won't start?**
```bash
# Check Python version
python3.12 --version

# Check if port is in use
sudo lsof -i :8000

# View error logs
tail -f logs/server.log
```

**Can't access from browser?**
```bash
# Check firewall
sudo ufw status
sudo ufw allow 8000/tcp

# Check if server is running
ps aux | grep start_server
```

**Need to restart?**
```bash
# Kill server
pkill -f start_server.py

# Start again
./start_ubuntu.sh
```

---

## 📞 Support

For detailed help, see `DEPLOYMENT_UBUNTU.md`
