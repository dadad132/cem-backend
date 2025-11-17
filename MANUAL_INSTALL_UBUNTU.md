# Manual Ubuntu Installation Guide

## Simple step-by-step installation without virtual environment

### 1. Install system packages
```bash
sudo apt update
sudo apt install -y python3 python3-pip git sqlite3
```

### 2. Clone the repository
```bash
cd ~
git clone https://github.com/dadad132/cem-backend.git
cd cem-backend
```

### 3. Install Python dependencies (skip errors for system packages)
```bash
pip3 install --break-system-packages -r requirements.txt 2>&1 | grep -v "ERROR: Cannot uninstall"
```
Or ignore all errors:
```bash
pip3 install --break-system-packages -r requirements.txt || true
```

### 4. Create directories
```bash
mkdir -p logs backups app/uploads/comments updates
```

### 5. Create .env file
```bash
cat > .env << 'EOF'
DATABASE_URL=sqlite+aiosqlite:///./data.db
SECRET_KEY=change-this-secret-key-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
HOST=0.0.0.0
PORT=8000
EOF
```

### 6. Test if it runs
```bash
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Press Ctrl+C to stop it once you confirm it works.

### 7. Create systemd service (optional - for auto-start)
```bash
sudo tee /etc/systemd/system/crm-backend.service > /dev/null << EOF
[Unit]
Description=CRM Backend Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/cem-backend
ExecStart=/usr/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

### 8. Enable and start the service
```bash
sudo systemctl daemon-reload
sudo systemctl enable crm-backend
sudo systemctl start crm-backend
```

### 9. Check if it's running
```bash
sudo systemctl status crm-backend
```

### 10. View logs if needed
```bash
sudo journalctl -u crm-backend -f
```

## Quick commands

**Start:** `sudo systemctl start crm-backend`  
**Stop:** `sudo systemctl stop crm-backend`  
**Restart:** `sudo systemctl restart crm-backend`  
**Logs:** `sudo journalctl -u crm-backend -f`

## Update the application

```bash
cd ~/cem-backend
git pull origin main
sudo systemctl restart crm-backend
```

## Troubleshooting

If you get import errors, install the missing package:
```bash
pip3 install --break-system-packages package-name
```

Common packages you might need:
```bash
pip3 install --break-system-packages sqlmodel aiosqlite python-multipart python-jose passlib bcrypt
```
