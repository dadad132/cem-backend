#!/bin/bash

###############################################################################
# CEM / CRM Backend - All-in-One MeshCentral Server Installer
#
# Installs and configures a self-hosted MeshCentral server right alongside
# CEM (or on its own VPS) with:
#   - Node.js 20 LTS + MeshCentral in /opt/meshcentral
#   - Reverse proxy on Nginx (ports 80/443 -> 127.0.0.1:4430) with WebSockets
#   - Free Let's Encrypt HTTPS certificate (coexists cleanly with tickets.hubconnect.co.za)
#   - Auto-configured config.json (AllowFraming, AllowLoginToken, TlsOffload, certUrl)
#   - Automatic admin account creation + Login Token Key generation
#   - systemd service (meshcentral.service) enabled on boot
#
# Works on AlmaLinux/RHEL/Rocky (dnf) and Ubuntu/Debian (apt).
#
# Usage:
#   sudo bash setup_meshcentral.sh --domain mesh.hubconnect.co.za --email admin@hubconnect.co.za
#   sudo bash setup_meshcentral.sh --domain mesh.hubconnect.co.za --no-ssl   # HTTP only for testing
#
# Options:
#   -d, --domain NAME       Domain for MeshCentral (default: mesh.hubconnect.co.za)
#   -e, --email ADDRESS     Email for Let's Encrypt SSL renewal notices
#   -u, --admin-user NAME   MeshCentral admin username (default: admin)
#   -p, --admin-pass PASS   MeshCentral admin password (auto-generated if omitted)
#       --port PORT         Internal MeshCentral port (default: 4430)
#       --dir PATH          Install directory (default: /opt/meshcentral)
#       --no-ssl            Configure over plain HTTP only (not recommended for production)
#       --staging           Use Let's Encrypt staging CA (for testing)
#       --skip-dns-check    Continue even if DNS does not point here yet
#   -h, --help              Show this help
###############################################################################

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_error()  { echo -e "${RED}[✗]${NC} $1"; }
print_info()   { echo -e "${YELLOW}[i]${NC} $1"; }

DEFAULT_DOMAIN="mesh.hubconnect.co.za"
DOMAIN=""
EMAIL=""
ADMIN_USER="admin"
ADMIN_PASS=""
MC_PORT=4430
MC_DIR="/opt/meshcentral"
MC_USER="meshcentral"
SERVICE_NAME="meshcentral"
NGINX_CONF="/etc/nginx/conf.d/meshcentral.conf"
USE_SSL=1
STAGING=0
SKIP_DNS=0

echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}   MeshCentral Installer for CEM Remote Access${NC}"
echo -e "${BLUE}================================================${NC}\n"

# ─── Parse arguments ─────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        -d|--domain)       DOMAIN="$2"; shift 2 ;;
        -e|--email)        EMAIL="$2"; shift 2 ;;
        -u|--admin-user)   ADMIN_USER="$2"; shift 2 ;;
        -p|--admin-pass)   ADMIN_PASS="$2"; shift 2 ;;
        --port)            MC_PORT="$2"; shift 2 ;;
        --dir)             MC_DIR="$2"; shift 2 ;;
        --no-ssl)          USE_SSL=0; shift ;;
        --staging)         STAGING=1; shift ;;
        --skip-dns-check)  SKIP_DNS=1; shift ;;
        -h|--help)         sed -n '3,32p' "$0"; exit 0 ;;
        *) print_error "Unknown option: $1"; exit 1 ;;
    esac
done

# ─── Root check ──────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    print_error "This script must run as root."
    print_info  "Try:  sudo bash $0 $*"
    exit 1
fi

# ─── Prompt for missing required values ──────────────────────────────────────
if [ -z "$DOMAIN" ]; then
    read -r -p "Domain for MeshCentral [${DEFAULT_DOMAIN}]: " DOMAIN
    DOMAIN="${DOMAIN:-$DEFAULT_DOMAIN}"
fi

if [ "$USE_SSL" -eq 1 ] && [ -z "$EMAIL" ]; then
    read -r -p "Email for Let's Encrypt SSL notices (blank to skip HTTPS): " EMAIL
    if [ -z "$EMAIL" ]; then
        USE_SSL=0
        print_info "No email given - configuring HTTP only"
    fi
fi

if [ -z "$ADMIN_PASS" ]; then
    ADMIN_PASS=$(tr -dc 'A-Za-z0-9@#%+=_' </dev/urandom | head -c 20 || openssl rand -base64 16 | tr -dc 'A-Za-z0-9')
    print_info "Generated strong random password for MeshCentral user '${ADMIN_USER}'"
fi

print_info "Domain:       $DOMAIN"
print_info "Install dir:  $MC_DIR"
print_info "Local port:   127.0.0.1:${MC_PORT}"
print_info "HTTPS:        $([ "$USE_SSL" -eq 1 ] && echo yes || echo 'no (plain HTTP)')"

# ─── Package manager ─────────────────────────────────────────────────────────
if command -v dnf >/dev/null 2>&1; then
    PKG="dnf"
elif command -v apt-get >/dev/null 2>&1; then
    PKG="apt"
else
    print_error "Neither dnf nor apt-get found - unsupported Linux distribution"
    exit 1
fi
print_info "Package manager: $PKG"

# ─── DNS sanity check ────────────────────────────────────────────────────────
if [ "$SKIP_DNS" -eq 0 ]; then
    SERVER_IP=$(curl -fsS --max-time 8 https://ifconfig.me 2>/dev/null || echo "")
    DOMAIN_IP=$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1; exit}' || echo "")
    if [ -z "$DOMAIN_IP" ]; then
        print_error "$DOMAIN does not resolve to any IP address yet."
        print_info  "Add a DNS A record pointing ${DOMAIN} -> ${SERVER_IP:-<this server IP>}, then re-run."
        print_info  "To proceed without DNS verification: --skip-dns-check"
        [ "$USE_SSL" -eq 1 ] && exit 1
    else
        LOCAL_IPS=$(hostname -I 2>/dev/null || echo "")
        if [ "$DOMAIN_IP" = "$SERVER_IP" ] || echo " $LOCAL_IPS " | grep -q " $DOMAIN_IP "; then
            print_status "DNS: $DOMAIN -> $DOMAIN_IP (points at this server)"
        else
            print_error "$DOMAIN resolves to $DOMAIN_IP (this server: ${SERVER_IP:-unknown})"
            print_info  "Point the DNS A record here first, or pass --skip-dns-check if behind NAT/port-forwarding."
            [ "$USE_SSL" -eq 1 ] && exit 1
        fi
    fi
fi

# ─── Install Node.js 20 LTS, nginx, and certbot ──────────────────────────────
NEED_NODE=1
if command -v node >/dev/null 2>&1; then
    NODE_MAJOR=$(node -v | sed -E 's/^v([0-9]+).*/\1/')
    if [ "${NODE_MAJOR:-0}" -ge 18 ]; then
        NEED_NODE=0
        print_status "Node.js $(node -v) already installed"
    fi
fi

if [ "$PKG" = "dnf" ]; then
    dnf install -y curl ca-certificates nginx >/dev/null
    if [ "$USE_SSL" -eq 1 ]; then
        dnf install -y epel-release >/dev/null 2>&1 || true
        dnf install -y certbot python3-certbot-nginx >/dev/null
    fi
    if [ "$NEED_NODE" -eq 1 ]; then
        print_info "Installing Node.js 20 LTS..."
        curl -fsSL https://rpm.nodesource.com/setup_20.x | bash - >/dev/null
        dnf install -y nodejs >/dev/null
        print_status "Node.js $(node -v) installed"
    fi
else
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates gnupg nginx >/dev/null
    if [ "$USE_SSL" -eq 1 ]; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y certbot python3-certbot-nginx >/dev/null
    fi
    if [ "$NEED_NODE" -eq 1 ]; then
        print_info "Installing Node.js 20 LTS..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
        DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs >/dev/null
        print_status "Node.js $(node -v) installed"
    fi
fi
print_status "nginx and dependencies ready"

# ─── Create dedicated user & install MeshCentral ─────────────────────────────
if ! id -u "$MC_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$MC_DIR" --shell /usr/sbin/nologin "$MC_USER"
    print_status "Created system user '$MC_USER'"
fi

# If crm-backend runs under its own service user, add it to meshcentral's group
# so CEM's 1-click "Auto-Setup & Fill" can read /opt/meshcentral seamlessly.
CRM_USER=$(awk -F= '/^User=/{print $2}' /etc/systemd/system/crm-backend.service 2>/dev/null || true)
if [ -n "$CRM_USER" ] && [ "$CRM_USER" != "root" ] && id -u "$CRM_USER" >/dev/null 2>&1; then
    usermod -aG "$MC_USER" "$CRM_USER" 2>/dev/null || true
fi

mkdir -p "$MC_DIR/meshcentral-data" "$MC_DIR/meshcentral-files" "$MC_DIR/meshcentral-backups"
chown -R "$MC_USER:$MC_USER" "$MC_DIR"

if [ ! -f "$MC_DIR/package.json" ]; then
    cat > "$MC_DIR/package.json" << 'EOF'
{
  "name": "cem-meshcentral",
  "version": "1.0.0",
  "private": true,
  "dependencies": {}
}
EOF
    chown "$MC_USER:$MC_USER" "$MC_DIR/package.json"
fi

print_info "Installing / updating MeshCentral via npm (this takes ~20-40s)..."
su -s /bin/bash "$MC_USER" -c "cd '$MC_DIR' && npm install --no-audit --no-fund meshcentral" >/tmp/meshcentral-npm.log 2>&1
print_status "MeshCentral installed in $MC_DIR"

# ─── Configure Nginx reverse proxy first so SSL cert can be issued ───────────
print_info "Writing Nginx reverse proxy config ($NGINX_CONF)..."
cat > "$NGINX_CONF" << EOF
# MeshCentral reverse proxy for CEM Remote Access
# Generated by setup_meshcentral.sh. Coexists cleanly with crm-backend.conf.

map \$http_upgrade \$mc_connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    client_max_body_size 100M;

    access_log /var/log/nginx/meshcentral.access.log;
    error_log  /var/log/nginx/meshcentral.error.log;

    location / {
        proxy_pass http://127.0.0.1:${MC_PORT};
        proxy_http_version 1.1;

        # WebSockets are required for /control.ashx, /agent.ashx, and /meshrelay.ashx
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        \$mc_connection_upgrade;

        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-Host  \$host:\$server_port;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Keep remote desktop & agent tunnels open without premature timeouts
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_send_timeout    3600s;
        proxy_read_timeout    3600s;
    }
}
EOF

if ! nginx -t >/tmp/meshcentral-nginx-test.log 2>&1; then
    print_error "nginx configuration test failed:"
    sed 's/^/    /' /tmp/meshcentral-nginx-test.log
    rm -f "$NGINX_CONF"
    exit 1
fi
print_status "nginx configuration valid"

# SELinux: allow nginx to connect to 127.0.0.1:4430
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" != "Disabled" ]; then
    setsebool -P httpd_can_network_connect 1 || true
    print_status "SELinux httpd_can_network_connect enabled"
fi

systemctl enable nginx >/dev/null 2>&1 || true
systemctl reload nginx || systemctl restart nginx
print_status "nginx reloaded"

# Firewall: ensure 80 and 443 are open
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-service=http  >/dev/null || true
    firewall-cmd --permanent --add-service=https >/dev/null || true
    firewall-cmd --reload >/dev/null || true
    print_status "Firewall ports 80/443 open (firewalld)"
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 80/tcp  >/dev/null || true
    ufw allow 443/tcp >/dev/null || true
    print_status "Firewall ports 80/443 open (ufw)"
fi

# ─── Request Let's Encrypt certificate before starting MeshCentral ───────────
# Issuing TLS first lets MeshCentral read Nginx's real certificate via certUrl
# on first startup so MeshAgents trust the exact Let's Encrypt cert hash.
if [ "$USE_SSL" -eq 1 ]; then
    print_info "Requesting Let's Encrypt SSL certificate for ${DOMAIN}..."
    CERTBOT_ARGS=(--nginx --non-interactive --agree-tos --redirect
                  --email "$EMAIL" --keep-until-expiring -d "$DOMAIN")
    [ "$STAGING" -eq 1 ] && CERTBOT_ARGS+=(--staging)
    if certbot "${CERTBOT_ARGS[@]}"; then
        print_status "SSL certificate installed for https://${DOMAIN}"
    else
        print_error "certbot failed - falling back to plain HTTP for now"
        print_info  "Once DNS points here, run: certbot --nginx -d ${DOMAIN}"
        USE_SSL=0
    fi
fi

# ─── Write MeshCentral config.json ───────────────────────────────────────────
CFG_FILE="$MC_DIR/meshcentral-data/config.json"
if [ -f "$CFG_FILE" ]; then
    cp -f "$CFG_FILE" "${CFG_FILE}.bak-$(date +%Y%m%d%H%M%S)"
fi

if [ "$USE_SSL" -eq 1 ]; then
    cat > "$CFG_FILE" << EOF
{
  "settings": {
    "Cert": "${DOMAIN}",
    "Port": ${MC_PORT},
    "AliasPort": 443,
    "RedirPort": 0,
    "MpsPort": 0,
    "TlsOffload": "127.0.0.1",
    "TrustedProxy": "127.0.0.1,::1",
    "AgentPong": 300,
    "AllowFraming": true,
    "AllowLoginToken": true
  },
  "domains": {
    "": {
      "Title": "Remote Support",
      "NewAccounts": false,
      "certUrl": "https://${DOMAIN}:443/"
    }
  }
}
EOF
else
    cat > "$CFG_FILE" << EOF
{
  "settings": {
    "Cert": "${DOMAIN}",
    "Port": ${MC_PORT},
    "AliasPort": 80,
    "RedirPort": 0,
    "MpsPort": 0,
    "TlsOffload": "127.0.0.1",
    "TrustedProxy": "127.0.0.1,::1",
    "AgentPong": 300,
    "AllowFraming": true,
    "AllowLoginToken": true
  },
  "domains": {
    "": {
      "Title": "Remote Support",
      "NewAccounts": false
    }
  }
}
EOF
fi
chown "$MC_USER:$MC_USER" "$CFG_FILE"
chmod 660 "$CFG_FILE"
print_status "Wrote $CFG_FILE (AllowFraming=true, AllowLoginToken=true, TlsOffload=127.0.0.1)"

# ─── Stop existing service (if any) before offline account & token setup ─────
systemctl stop "$SERVICE_NAME" 2>/dev/null || true

# ─── Create admin account + read Login Token Key (offline, zero DB lock) ─────
print_info "Creating MeshCentral admin account '${ADMIN_USER}'..."
su -s /bin/bash "$MC_USER" -c \
    "cd '$MC_DIR' && node node_modules/meshcentral --createaccount '$ADMIN_USER' --pass '$ADMIN_PASS'" \
    >/tmp/meshcentral-user.log 2>&1 || true
su -s /bin/bash "$MC_USER" -c \
    "cd '$MC_DIR' && node node_modules/meshcentral --adminaccount '$ADMIN_USER'" \
    >>/tmp/meshcentral-user.log 2>&1 || true
print_status "Admin account '${ADMIN_USER}' ready"

print_info "Retrieving MeshCentral Login Token Key..."
LOGIN_TOKEN_KEY=$(su -s /bin/bash "$MC_USER" -c \
    "cd '$MC_DIR' && node node_modules/meshcentral --loginTokenKey" 2>/dev/null \
    | grep -Eo '[0-9a-fA-F]{64,256}' | head -n 1 || true)
if [ -n "$LOGIN_TOKEN_KEY" ]; then
    print_status "Login Token Key retrieved"
else
    print_info "Login Token Key not printed yet — CEM's automatic live SSO will still work with Username + Password"
fi

# Ensure CEM service group can read/traverse /opt/meshcentral for Auto-Setup
chmod 750 "$MC_DIR" "$MC_DIR/meshcentral-data"

# ─── Create and start systemd service ────────────────────────────────────────
NODE_BIN=$(command -v node)
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=MeshCentral Remote Support Server
After=network.target nginx.service

[Service]
Type=simple
User=${MC_USER}
Group=${MC_USER}
WorkingDirectory=${MC_DIR}
Environment=NODE_ENV=production
ExecStart=${NODE_BIN} ${MC_DIR}/node_modules/meshcentral
Restart=always
RestartSec=10
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
systemctl restart "$SERVICE_NAME"

print_info "Waiting for MeshCentral to start on 127.0.0.1:${MC_PORT}..."
MC_OK=0
for _ in $(seq 1 40); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${MC_PORT}/" >/dev/null 2>&1; then
        MC_OK=1
        break
    fi
    sleep 1
done

if [ "$MC_OK" -ne 1 ]; then
    print_error "MeshCentral did not respond on 127.0.0.1:${MC_PORT}"
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager || true
    exit 1
fi
print_status "MeshCentral service is running"

# ─── Link CEM .env so CEM Auto-Setup finds /opt/meshcentral automatically ────
CRM_DIR=$(awk -F= '/^WorkingDirectory=/{print $2}' /etc/systemd/system/crm-backend.service 2>/dev/null || true)
if [ -n "$CRM_DIR" ] && [ -f "$CRM_DIR/.env" ]; then
    if grep -q '^MESHCENTRAL_DIR=' "$CRM_DIR/.env"; then
        sed -i "s|^MESHCENTRAL_DIR=.*|MESHCENTRAL_DIR=${MC_DIR}|" "$CRM_DIR/.env"
    else
        echo "MESHCENTRAL_DIR=${MC_DIR}" >> "$CRM_DIR/.env"
    fi
    print_status "Linked MESHCENTRAL_DIR=${MC_DIR} in ${CRM_DIR}/.env"
fi

# ─── Save credentials summary for the admin (root-only permissions) ──────────
SCHEME=$([ "$USE_SSL" -eq 1 ] && echo https || echo http)
SERVER_URL="${SCHEME}://${DOMAIN}"
CRED_FILE="/root/meshcentral-credentials.txt"
cat > "$CRED_FILE" << EOF
MeshCentral Server Credentials (generated $(date -u +%Y-%m-%dT%H:%M:%SZ))
======================================================================
Server URL:      ${SERVER_URL}
Username:        ${ADMIN_USER}
Password:        ${ADMIN_PASS}
Login Token Key: ${LOGIN_TOKEN_KEY:-"(use Auto-Setup & Fill in CEM or run: node ${MC_DIR}/node_modules/meshcentral --loginTokenKey)"}
Install Path:    ${MC_DIR}
Config File:     ${CFG_FILE}
EOF
chmod 600 "$CRED_FILE"

echo ""
echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}   MeshCentral Installation Complete!${NC}"
echo -e "${GREEN}================================================${NC}\n"
echo -e "${BLUE}Enter these in CEM -> Admin -> Site Settings (MeshCentral Remote Access):${NC}"
echo -e "  Server URL:      ${GREEN}${SERVER_URL}${NC}"
echo -e "  Username:        ${GREEN}${ADMIN_USER}${NC}"
echo -e "  Password:        ${GREEN}${ADMIN_PASS}${NC}"
if [ -n "$LOGIN_TOKEN_KEY" ]; then
echo -e "  Login Token Key: ${GREEN}${LOGIN_TOKEN_KEY}${NC}"
fi
echo ""
echo -e "${YELLOW}Tip:${NC} Just enter URL + Username + Password in CEM Site Settings and click"
echo -e "     ${BLUE}Auto-Setup & Fill${NC} — CEM will auto-configure SSO and create your default group!"
echo ""
echo -e "Saved a copy to: ${YELLOW}${CRED_FILE}${NC} (root-only)"
echo -e "Service status:  ${YELLOW}systemctl status ${SERVICE_NAME}${NC}"
echo -e "Service logs:    ${YELLOW}journalctl -u ${SERVICE_NAME} -f${NC}"
echo ""
