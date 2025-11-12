# 🚀 Quick Start Guide - Local IP Server

## Your Machine Info
Based on detection, your machine has:
- **Hostname:** VRY-CAD6
- **Primary IP:** 10.0.0.15
- **Additional IPs:** 192.168.56.1

## Start Server Commands

### 1. Auto-detect and use local IP (RECOMMENDED for data center)
```bash
python start_server.py
```
Server will be accessible at: http://10.0.0.15:8000

### 2. Start with Windows batch file
```bash
start.bat
```

### 3. Use a specific IP
```bash
python start_server.py --host 10.0.0.15 --port 8000
```

### 4. Bind to ALL network interfaces
```bash
python start_server.py --all-interfaces
```
Makes server accessible on:
- http://10.0.0.15:8000
- http://192.168.56.1:8000
- http://localhost:8000

### 5. Use different port
```bash
python start_server.py --port 3000
```

## Access the Server

From the same machine:
- http://localhost:8000
- http://10.0.0.15:8000

From other machines on the network:
- http://10.0.0.15:8000

## Stop the Server
Press `CTRL+C` in the terminal

## Troubleshooting

**Can't access from other machines?**
1. Use `python start_server.py --all-interfaces`
2. Check Windows Firewall
3. Make sure port 8000 is open

**Need to check available IPs?**
```bash
python -m app.core.network
```

## Data Center Deployment

Simply run:
```bash
python start_server.py
```

The script will automatically:
✅ Detect the data center's assigned local IP
✅ Bind the server to that IP
✅ Make it accessible across the network
✅ Display the access URL

No manual IP configuration needed!
