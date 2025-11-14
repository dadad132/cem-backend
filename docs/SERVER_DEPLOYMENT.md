# Server Deployment Guide

## Local IP Auto-Detection

This CRM backend server automatically detects the machine's local IP address and binds to it, making it accessible across your local network or data center.

## Quick Start

### Option 1: Using the startup script (Recommended)

**Windows:**
```bash
start.bat
```

**Linux/Mac:**
```bash
chmod +x start.sh
./start.sh
```

**Direct Python:**
```bash
python start_server.py
```

The server will:
1. Automatically detect your local IP address
2. Start the server on that IP
3. Display all available network interfaces
4. Show the URL where the server can be accessed

### Option 2: Manual IP specification

If you want to specify a particular IP address:

```bash
python start_server.py --host 192.168.1.100 --port 8000
```

### Option 3: Bind to all interfaces

To make the server accessible on ALL network interfaces (useful for multiple network cards):

```bash
python start_server.py --all-interfaces
```

This binds to `0.0.0.0`, making the server accessible on all IPs.

## Command Line Options

```
python start_server.py [OPTIONS]

Options:
  --host HOST              Specify host IP address (default: auto-detect)
  --port PORT              Specify port number (default: 8000)
  --all-interfaces         Bind to all interfaces (0.0.0.0)
  -h, --help              Show help message
```

## Testing Local IP Detection

To see what IP addresses are available on your machine:

```bash
python -m app.core.network
```

This will display:
- Your hostname
- Primary local IP (the one the server will use)
- All available local IPs on the machine

## Data Center Deployment

When deploying to a data center:

1. **Automatic Detection (Recommended):**
   ```bash
   python start_server.py
   ```
   The server will automatically detect and use the data center's assigned local IP.

2. **Manual Configuration:**
   If you know the specific IP assigned to your machine:
   ```bash
   python start_server.py --host 10.0.1.50 --port 8000
   ```

3. **Multiple Network Interfaces:**
   If your data center machine has multiple network cards:
   ```bash
   python start_server.py --all-interfaces
   ```

## Firewall Configuration

Ensure your firewall allows incoming connections on the server port (default 8000):

**Windows:**
```powershell
New-NetFirewallRule -DisplayName "CRM Backend" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

**Linux (iptables):**
```bash
sudo iptables -A INPUT -p tcp --dport 8000 -j ACCEPT
```

**Linux (firewalld):**
```bash
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --reload
```

## Troubleshooting

### Can't access server from other machines?

1. Check firewall settings
2. Verify the server is bound to the correct IP (not 127.0.0.1)
3. Ensure other machines are on the same network
4. Try using `--all-interfaces` flag

### Server shows 127.0.0.1?

This means the auto-detection couldn't find a network interface. Try:
1. Check your network connection
2. Manually specify the IP with `--host` flag
3. Use `python -m app.core.network` to see available IPs

### Multiple IPs shown?

Your machine has multiple network interfaces. The server automatically picks the primary one, but you can specify a different one using the `--host` flag.

## Production Deployment

For production deployment, consider using a process manager:

**Using systemd (Linux):**

Create `/etc/systemd/system/crm-backend.service`:
```ini
[Unit]
Description=CRM Backend Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/crm-backend
ExecStart=/usr/bin/python3 /path/to/crm-backend/start_server.py --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable crm-backend
sudo systemctl start crm-backend
```

**Using PM2 (Node.js process manager):**
```bash
pm2 start start_server.py --name crm-backend --interpreter python3
pm2 save
pm2 startup
```

## Environment Variables

You can also use environment variables:

```bash
export CRM_HOST=192.168.1.100
export CRM_PORT=8000
python start_server.py
```

Then modify `start_server.py` to read these variables if needed.
