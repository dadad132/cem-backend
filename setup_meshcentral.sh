#!/bin/bash

###############################################################################
# CEM / CRM Backend - Integrated MeshCentral Engine Installer
#
# Installs MeshCentral as a locked-down background engine on the SAME server
# and SAME domain as your CEM website (tickets.hubconnect.co.za) — NO second
# server and NO extra DNS record needed!
#
# Security & Single-Website Integration:
#   - Anyone visiting MeshCentral directly is immediately redirected (302) to
#     https://tickets.hubconnect.co.za/web/remote so staff MUST sign in to your
#     website first.
#   - Only your website (tickets.hubconnect.co.za) is allowed to frame remote
#     desktop sessions (CSP frame-ancestors).
#   - Client downloads are proxied through https://tickets.hubconnect.co.za/web/remote/get/...
#
# Two Modes (both on your single existing server):
#   1. Same-Domain Mode (DEFAULT — zero extra DNS records!):
#      Reuses tickets.hubconnect.co.za + its existing Let's Encrypt SSL cert
#      on Nginx port 4433 -> local MeshCentral 127.0.0.1:4430.
#        sudo bash setup_meshcentral.sh --site-domain tickets.hubconnect.co.za
#
#   2. Subdomain Port-443 Mode (optional — best for strict corporate firewalls):
#      Uses a second DNS A record (e.g. mesh.hubconnect.co.za -> same server IP)
#      on standard port 443, still locked down to redirect direct visits to CEM.
#        sudo bash setup_meshcentral.sh --domain mesh.hubconnect.co.za \
#             --site-domain tickets.hubconnect.co.za --email admin@hubconnect.co.za
#
# Works on AlmaLinux/RHEL/Rocky (dnf) and Ubuntu/Debian (apt).
#
# Options:
#       --site-domain NAME  Your main CEM website domain (default: tickets.hubconnect.co.za)
#   -d, --domain NAME       MeshCentral engine domain (default: same as --site-domain)
#       --public-port PORT  External HTTPS port for same-domain mode (default: 4433)
#   -e, --email ADDRESS     Email for Let's Encrypt (only needed for separate --domain)
#   -u, --admin-user NAME   Internal engine admin username (default: admin)
#   -p, --admin-pass PASS   Internal engine admin password (auto-generated if omitted)
#       --port PORT         Internal loopback MeshCentral port (default: 4430)
#       --dir PATH          Install directory (default: /opt/meshcentral)
#       --no-ssl            Plain HTTP only (testing only)
#       --staging           Use Let's Encrypt staging CA
#       --skip-dns-check    Skip DNS check
#   -h, --help              Show this help
###############################################################################

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_error()  { echo -e "${RED}[✗]${NC} $1"; }
print_info()   { echo -e "${YELLOW}[i]${NC} $1"; }

SITE_DOMAIN="tickets.hubconnect.co.za"
DOMAIN=""
PUBLIC_PORT=""
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

echo -e "${BLUE}=====================================================${NC}"
echo -e "${BLUE}   MeshCentral Integrated Engine Installer for CEM${NC}"
echo -e "${BLUE}=====================================================${NC}\n"

# ─── Parse arguments ─────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        --site-domain)     SITE_DOMAIN="$2"; shift 2 ;;
        -d|--domain)       DOMAIN="$2"; shift 2 ;;
        --public-port)     PUBLIC_PORT="$2"; shift 2 ;;
        -e|--email)        EMAIL="$2"; shift 2 ;;
        -u|--admin-user)   ADMIN_USER="$2"; shift 2 ;;
        -p|--admin-pass)   ADMIN_PASS="$2"; shift 2 ;;
        --port)            MC_PORT="$2"; shift 2 ;;
        --dir)             MC_DIR="$2"; shift 2 ;;
        --no-ssl)          USE_SSL=0; shift ;;
        --staging)         STAGING=1; shift ;;
        --skip-dns-check)  SKIP_DNS=1; shift ;;
        -h|--help)         sed -n '3,46p' "$0"; exit 0 ;;
        *) print_error "Unknown option: $1"; exit 1 ;;
    esac
done

# ─── Root check ──────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    print_error "This script must run as root."
    print_info  "Try:  sudo bash $0 $*"
    exit 1
fi

# Default to same-domain mode (NO extra DNS record needed!)
if [ -z "$DOMAIN" ]; then
    DOMAIN="$SITE_DOMAIN"
fi

SAME_DOMAIN=0
if [ "$DOMAIN" = "$SITE_DOMAIN" ]; then
    SAME_DOMAIN=1
    PUBLIC_PORT="${PUBLIC_PORT:-4433}"
else
    PUBLIC_PORT="${PUBLIC_PORT:-443}"
fi

if [ "$SAME_DOMAIN" -eq 0 ] && [ "$USE_SSL" -eq 1 ] && [ -z "$EMAIL" ]; then
    read -r -p "Email for Let's Encrypt SSL notices (blank to skip HTTPS): " EMAIL
    if [ -z "$EMAIL" ]; then
        USE_SSL=0
        print_info "No email given - configuring HTTP only"
    fi
fi

if [ -z "$ADMIN_PASS" ]; then
    ADMIN_PASS=$(tr -dc 'A-Za-z0-9@#%+=_' </dev/urandom | head -c 20 || openssl rand -base64 16 | tr -dc 'A-Za-z0-9')
    print_info "Generated strong random internal password for engine user '${ADMIN_USER}'"
fi

SITE_ORIGIN="https://${SITE_DOMAIN}"
[ "$USE_SSL" -eq 0 ] && SITE_ORIGIN="http://${SITE_DOMAIN}"

print_info "Main CEM site:   ${SITE_ORIGIN}"
print_info "Engine mode:     $([ "$SAME_DOMAIN" -eq 1 ] && echo "Same-domain (${DOMAIN}:${PUBLIC_PORT} — no extra DNS needed!)" || echo "Dedicated subdomain (${DOMAIN}:${PUBLIC_PORT})")"
print_info "Install dir:     ${MC_DIR}"
print_info "Loopback port:   127.0.0.1:${MC_PORT}"
print_info "HTTPS:           $([ "$USE_SSL" -eq 1 ] && echo yes || echo 'no (plain HTTP)')"

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

# ─── Certificate check in Same-Domain mode ───────────────────────────────────
CERT_FULLCHAIN="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
CERT_PRIVKEY="/etc/letsencrypt/live/${DOMAIN}/privkey.pem"

if [ "$SAME_DOMAIN" -eq 1 ] && [ "$USE_SSL" -eq 1 ]; then
    if [ ! -f "$CERT_FULLCHAIN" ] || [ ! -f "$CERT_PRIVKEY" ]; then
        print_error "No Let's Encrypt SSL certificate found yet for ${DOMAIN} (${CERT_FULLCHAIN})."
        print_info  "Run the website domain setup first:"
        print_info  "  sudo bash setup_domain_ssl.sh --domain ${DOMAIN} --email your@email.com"
        print_info  "Then re-run this script (or pass --no-ssl for HTTP testing)."
        exit 1
    fi
    print_status "Reusing existing SSL certificate for ${DOMAIN}"
fi

# ─── DNS sanity check (for separate subdomain mode) ──────────────────────────
if [ "$SAME_DOMAIN" -eq 0 ] && [ "$SKIP_DNS" -eq 0 ]; then
    SERVER_IP=$(curl -fsS --max-time 8 https://ifconfig.me 2>/dev/null || echo "")
    DOMAIN_IP=$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1; exit}' || echo "")
    if [ -z "$DOMAIN_IP" ]; then
        print_error "$DOMAIN does not resolve to any IP address yet."
        print_info  "Add a DNS A record pointing ${DOMAIN} -> ${SERVER_IP:-<this server IP>}, then re-run."
        print_info  "Or run without --domain to reuse ${SITE_DOMAIN} with ZERO extra DNS records!"
        [ "$USE_SSL" -eq 1 ] && exit 1
    else
        LOCAL_IPS=$(hostname -I 2>/dev/null || echo "")
        if [ "$DOMAIN_IP" = "$SERVER_IP" ] || echo " $LOCAL_IPS " | grep -q " $DOMAIN_IP "; then
            print_status "DNS: $DOMAIN -> $DOMAIN_IP (points at this server)"
        else
            print_error "$DOMAIN resolves to $DOMAIN_IP (this server: ${SERVER_IP:-unknown})"
            print_info  "Point the DNS A record here first, or pass --skip-dns-check."
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
    if [ "$SAME_DOMAIN" -eq 0 ] && [ "$USE_SSL" -eq 1 ]; then
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
    if [ "$SAME_DOMAIN" -eq 0 ] && [ "$USE_SSL" -eq 1 ]; then
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

# ─── Configure Nginx reverse proxy with standalone-login lockdown ────────────
# Direct visits to "/" without a CEM SSO token (?login=, ?auth=, ?gotonode=)
# are redirected straight to https://<SITE_DOMAIN>/web/remote so staff MUST
# sign into your CEM website first.
print_info "Writing locked-down Nginx proxy config ($NGINX_CONF)..."

if [ "$SAME_DOMAIN" -eq 1 ] && [ "$USE_SSL" -eq 1 ]; then
    cat > "$NGINX_CONF" << EOF
# Integrated MeshCentral Engine (same domain, port ${PUBLIC_PORT} TLS)
# Generated by setup_meshcentral.sh. Coexists cleanly with crm-backend.conf.

map \$http_upgrade \$mc_connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen ${PUBLIC_PORT} ssl;
    listen [::]:${PUBLIC_PORT} ssl;
    server_name ${DOMAIN};

    ssl_certificate     ${CERT_FULLCHAIN};
    ssl_certificate_key ${CERT_PRIVKEY};
    ssl_protocols       TLSv1.2 TLSv1.3;

    client_max_body_size 100M;

    # Only allow your CEM website to embed remote desktop sessions
    add_header Content-Security-Policy "frame-ancestors 'self' ${SITE_ORIGIN};" always;

    access_log /var/log/nginx/meshcentral.access.log;
    error_log  /var/log/nginx/meshcentral.error.log;

    location = / {
        # Block standalone logins: direct browser visits go straight to CEM
        set \$cem_allow 0;
        if (\$arg_login != "")    { set \$cem_allow 1; }
        if (\$arg_auth != "")     { set \$cem_allow 1; }
        if (\$arg_gotonode != "") { set \$cem_allow 1; }
        if (\$cem_allow = 0) {
            return 302 ${SITE_ORIGIN}/web/remote;
        }

        proxy_pass http://127.0.0.1:${MC_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        \$mc_connection_upgrade;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-Host  \$host:${PUBLIC_PORT};
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_send_timeout    3600s;
        proxy_read_timeout    3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:${MC_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        \$mc_connection_upgrade;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-Host  \$host:${PUBLIC_PORT};
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_send_timeout    3600s;
        proxy_read_timeout    3600s;
    }
}
EOF
elif [ "$SAME_DOMAIN" -eq 1 ] && [ "$USE_SSL" -eq 0 ]; then
    cat > "$NGINX_CONF" << EOF
# Integrated MeshCentral Engine (same domain, plain HTTP on port ${PUBLIC_PORT})
# Generated by setup_meshcentral.sh.

map \$http_upgrade \$mc_connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen ${PUBLIC_PORT};
    listen [::]:${PUBLIC_PORT};
    server_name ${DOMAIN};

    client_max_body_size 100M;
    add_header Content-Security-Policy "frame-ancestors 'self' ${SITE_ORIGIN};" always;

    access_log /var/log/nginx/meshcentral.access.log;
    error_log  /var/log/nginx/meshcentral.error.log;

    location = / {
        set \$cem_allow 0;
        if (\$arg_login != "")    { set \$cem_allow 1; }
        if (\$arg_auth != "")     { set \$cem_allow 1; }
        if (\$arg_gotonode != "") { set \$cem_allow 1; }
        if (\$cem_allow = 0) {
            return 302 ${SITE_ORIGIN}/web/remote;
        }

        proxy_pass http://127.0.0.1:${MC_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        \$mc_connection_upgrade;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-Host  \$host:${PUBLIC_PORT};
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_send_timeout    3600s;
        proxy_read_timeout    3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:${MC_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        \$mc_connection_upgrade;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-Host  \$host:${PUBLIC_PORT};
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_send_timeout    3600s;
        proxy_read_timeout    3600s;
    }
}
EOF
else
    cat > "$NGINX_CONF" << EOF
# Dedicated Subdomain MeshCentral Engine (locked down to CEM)
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
    add_header Content-Security-Policy "frame-ancestors 'self' ${SITE_ORIGIN};" always;

    access_log /var/log/nginx/meshcentral.access.log;
    error_log  /var/log/nginx/meshcentral.error.log;

    location = / {
        set \$cem_allow 0;
        if (\$arg_login != "")    { set \$cem_allow 1; }
        if (\$arg_auth != "")     { set \$cem_allow 1; }
        if (\$arg_gotonode != "") { set \$cem_allow 1; }
        if (\$cem_allow = 0) {
            return 302 ${SITE_ORIGIN}/web/remote;
        }

        proxy_pass http://127.0.0.1:${MC_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        \$mc_connection_upgrade;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-Host  \$host:\$server_port;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_send_timeout    3600s;
        proxy_read_timeout    3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:${MC_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        \$mc_connection_upgrade;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-Host  \$host:\$server_port;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_send_timeout    3600s;
        proxy_read_timeout    3600s;
    }
}
EOF
fi

# SELinux: allow nginx to connect to 127.0.0.1:4430 and bind custom HTTPS port (e.g. 4433)
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" != "Disabled" ]; then
    setsebool -P httpd_can_network_connect 1 || true
    if [ "$SAME_DOMAIN" -eq 1 ] && command -v semanage >/dev/null 2>&1; then
        semanage port -a -t http_port_t -p tcp "$PUBLIC_PORT" 2>/dev/null || \
        semanage port -m -t http_port_t -p tcp "$PUBLIC_PORT" 2>/dev/null || true
    fi
    print_status "SELinux permissions configured"
fi

if ! nginx -t >/tmp/meshcentral-nginx-test.log 2>&1; then
    print_error "nginx configuration test failed:"
    sed 's/^/    /' /tmp/meshcentral-nginx-test.log
    rm -f "$NGINX_CONF"
    exit 1
fi
print_status "nginx configuration valid"

systemctl enable nginx >/dev/null 2>&1 || true
systemctl reload nginx || systemctl restart nginx
print_status "nginx reloaded"

# Firewall: open 80, 443, and PUBLIC_PORT (if non-443)
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-service=http  >/dev/null || true
    firewall-cmd --permanent --add-service=https >/dev/null || true
    [ "$PUBLIC_PORT" -ne 443 ] && [ "$PUBLIC_PORT" -ne 80 ] && \
        firewall-cmd --permanent --add-port="${PUBLIC_PORT}/tcp" >/dev/null || true
    firewall-cmd --reload >/dev/null || true
    print_status "Firewall updated (firewalld)"
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 80/tcp  >/dev/null || true
    ufw allow 443/tcp >/dev/null || true
    [ "$PUBLIC_PORT" -ne 443 ] && [ "$PUBLIC_PORT" -ne 80 ] && \
        ufw allow "${PUBLIC_PORT}/tcp" >/dev/null || true
    print_status "Firewall updated (ufw)"
fi

# ─── Issue Let's Encrypt cert only in separate subdomain mode ────────────────
if [ "$SAME_DOMAIN" -eq 0 ] && [ "$USE_SSL" -eq 1 ]; then
    print_info "Requesting Let's Encrypt SSL certificate for ${DOMAIN}..."
    CERTBOT_ARGS=(--nginx --non-interactive --agree-tos --redirect
                  --email "$EMAIL" --keep-until-expiring -d "$DOMAIN")
    [ "$STAGING" -eq 1 ] && CERTBOT_ARGS+=(--staging)
    if certbot "${CERTBOT_ARGS[@]}"; then
        print_status "SSL certificate installed for https://${DOMAIN}"
    else
        print_error "certbot failed - falling back to plain HTTP for now"
        USE_SSL=0
    fi
fi

# ─── Write MeshCentral config.json ───────────────────────────────────────────
CFG_FILE="$MC_DIR/meshcentral-data/config.json"
if [ -f "$CFG_FILE" ]; then
    cp -f "$CFG_FILE" "${CFG_FILE}.bak-$(date +%Y%m%d%H%M%S)"
fi

ALIAS_PORT="$PUBLIC_PORT"
if [ "$USE_SSL" -eq 1 ]; then
    cat > "$CFG_FILE" << EOF
{
  "settings": {
    "Cert": "${DOMAIN}",
    "Port": ${MC_PORT},
    "AliasPort": ${ALIAS_PORT},
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
      "certUrl": "https://${DOMAIN}:${ALIAS_PORT}/"
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
    "AliasPort": ${ALIAS_PORT},
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
print_status "Wrote $CFG_FILE (AllowFraming=true, AllowLoginToken=true, AliasPort=${ALIAS_PORT})"

# ─── Stop existing service (if any) before offline account & token setup ─────
systemctl stop "$SERVICE_NAME" 2>/dev/null || true

print_info "Creating internal engine admin account '${ADMIN_USER}'..."
su -s /bin/bash "$MC_USER" -c \
    "cd '$MC_DIR' && node node_modules/meshcentral --createaccount '$ADMIN_USER' --pass '$ADMIN_PASS'" \
    >/tmp/meshcentral-user.log 2>&1 || true
su -s /bin/bash "$MC_USER" -c \
    "cd '$MC_DIR' && node node_modules/meshcentral --adminaccount '$ADMIN_USER'" \
    >>/tmp/meshcentral-user.log 2>&1 || true
print_status "Internal engine account '${ADMIN_USER}' ready"

print_info "Retrieving Login Token Key..."
LOGIN_TOKEN_KEY=$(su -s /bin/bash "$MC_USER" -c \
    "cd '$MC_DIR' && node node_modules/meshcentral --loginTokenKey" 2>/dev/null \
    | grep -Eo '[0-9a-fA-F]{64,256}' | head -n 1 || true)
if [ -n "$LOGIN_TOKEN_KEY" ]; then
    print_status "Login Token Key retrieved"
else
    print_info "Login Token Key will be auto-handled via CEM live SSO"
fi

chmod 750 "$MC_DIR" "$MC_DIR/meshcentral-data"

# ─── Create and start systemd service ────────────────────────────────────────
NODE_BIN=$(command -v node)
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=MeshCentral Integrated Remote Engine (CEM)
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

print_info "Waiting for MeshCentral engine on 127.0.0.1:${MC_PORT}..."
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
print_status "MeshCentral engine is running"

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

# ─── Build canonical engine URL and save credentials + CEM auto-link file ────
SCHEME=$([ "$USE_SSL" -eq 1 ] && echo https || echo http)
if [ "$USE_SSL" -eq 1 ] && [ "$PUBLIC_PORT" -eq 443 ]; then
    SERVER_URL="https://${DOMAIN}"
elif [ "$USE_SSL" -eq 0 ] && [ "$PUBLIC_PORT" -eq 80 ]; then
    SERVER_URL="http://${DOMAIN}"
else
    SERVER_URL="${SCHEME}://${DOMAIN}:${PUBLIC_PORT}"
fi

# Write a group-readable auto-link file so CEM automatically fills and enables
# MeshCentral on first visit (zero manual typing in Site Settings!).
AUTOLINK_FILE="$MC_DIR/meshcentral-data/cem-autolink.json"
MC_SERVER_URL="$SERVER_URL" \
MC_ADMIN_USER="$ADMIN_USER" \
MC_ADMIN_PASS="$ADMIN_PASS" \
MC_LOGIN_KEY="$LOGIN_TOKEN_KEY" \
MC_INSTALL_DIR="$MC_DIR" \
MC_VERIFY_SSL="$USE_SSL" \
python3 - "$AUTOLINK_FILE" << 'PYEOF'
import json, os, sys
payload = {
    "server_url": os.environ["MC_SERVER_URL"],
    "username": os.environ["MC_ADMIN_USER"],
    "password": os.environ["MC_ADMIN_PASS"],
    "login_token_key": os.environ.get("MC_LOGIN_KEY", ""),
    "local_install_path": os.environ["MC_INSTALL_DIR"],
    "verify_ssl": os.environ.get("MC_VERIFY_SSL", "1") == "1",
}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
    fh.write("\n")
PYEOF
chown "$MC_USER:$MC_USER" "$AUTOLINK_FILE"
chmod 640 "$AUTOLINK_FILE"
print_status "Wrote CEM auto-link config ($AUTOLINK_FILE) — zero manual typing needed!"

CRED_FILE="/root/meshcentral-credentials.txt"
cat > "$CRED_FILE" << EOF
MeshCentral Integrated Engine Credentials (generated $(date -u +%Y-%m-%dT%H:%M:%SZ))
======================================================================
Server URL:      ${SERVER_URL}
Username:        ${ADMIN_USER}
Password:        ${ADMIN_PASS}
Login Token Key: ${LOGIN_TOKEN_KEY:-"(auto-handled by CEM Auto-Setup & Fill)"}
Install Path:    ${MC_DIR}
Config File:     ${CFG_FILE}
Direct Access:   LOCKED — redirects straight to ${SITE_ORIGIN}/web/remote
EOF
chmod 600 "$CRED_FILE"

echo ""
echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN}   MeshCentral Integrated Engine Ready!${NC}"
echo -e "${GREEN}=====================================================${NC}\n"
echo -e "${BLUE}Direct browser access to MeshCentral is LOCKED:${NC}"
echo -e "  Anyone visiting ${SERVER_URL} directly is sent to ${GREEN}${SITE_ORIGIN}/web/remote${NC}"
echo ""
echo -e "${BLUE}Zero manual setup needed inside ${SITE_ORIGIN}:${NC}"
echo -e "  1. Restart CEM:  ${YELLOW}sudo systemctl restart crm-backend${NC}"
echo -e "  2. Open ${GREEN}${SITE_ORIGIN}/web/remote${NC} — CEM auto-imports all settings from"
echo -e "     ${AUTOLINK_FILE} and turns on Remote Support automatically!"
echo ""
echo -e "Credentials copy saved to: ${YELLOW}${CRED_FILE}${NC} (root-only)"
echo -e "Note: If your cloud VPS has an external firewall/security group, allow TCP port ${PUBLIC_PORT}."
echo ""
