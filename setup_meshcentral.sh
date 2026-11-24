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
#       --workspace-id ID   CEM workspace to seed (default: first workspace only)
#   -h, --help              Show this help
###############################################################################

set -euo pipefail

# This installer runs as root: never leave credentials or diagnostics readable
# by other users, and never follow a pre-planted symlink at a predictable /tmp
# path. Create the canonical log via mktemp + atomic replace (mv replaces the
# symlink itself rather than writing through it).
umask 077
VERIFY_LOG="/tmp/meshcentral-user.log"
_VERIFY_TMP=$(mktemp /tmp/meshcentral-verify.XXXXXX) || exit 1
mv -f -- "$_VERIFY_TMP" "$VERIFY_LOG"
chmod 600 "$VERIFY_LOG"

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
TARGET_WORKSPACE_ID=""
ORIGINAL_ARGS=("$@")

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
        -p*)               ADMIN_PASS="${1#-p}"; shift ;;
        --admin-pass=*)    ADMIN_PASS="${1#--admin-pass=}"; shift ;;
        --port)            MC_PORT="$2"; shift 2 ;;
        --dir)             MC_DIR="$2"; shift 2 ;;
        --no-ssl)          USE_SSL=0; shift ;;
        --staging)         STAGING=1; shift ;;
        --skip-dns-check)  SKIP_DNS=1; shift ;;
        --workspace-id)    TARGET_WORKSPACE_ID="$2"; shift 2 ;;
        -h|--help)         sed -n '3,47p' "$0"; exit 0 ;;
        *) print_error "Unknown option: $1"; exit 1 ;;
    esac
done

# ─── Root check ──────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    print_error "This script must run as root."
    # Never echo -p/--admin-pass values: local users can observe the hint.
    SAFE_ARGS=""
    HIDE_NEXT=0
    for arg in "${ORIGINAL_ARGS[@]}"; do
        if [ "$HIDE_NEXT" -eq 1 ]; then
            SAFE_ARGS="$SAFE_ARGS <redacted>"
            HIDE_NEXT=0
            continue
        fi
        case "$arg" in
            -p|--admin-pass) SAFE_ARGS="$SAFE_ARGS $arg"; HIDE_NEXT=1 ;;
            -p*|--admin-pass=*) SAFE_ARGS="$SAFE_ARGS <redacted>" ;;
            *) SAFE_ARGS="$SAFE_ARGS $arg" ;;
        esac
    done
    print_info  "Try:  sudo bash $0${SAFE_ARGS:-}"
    exit 1
fi

if [ -n "$TARGET_WORKSPACE_ID" ] && ! [[ "$TARGET_WORKSPACE_ID" =~ ^[0-9]+$ ]]; then
    print_error "--workspace-id must be a numeric CEM workspace ID."
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

# These values are embedded in commands executed as another user via `su -c`.
# Shell-escape them once here so quotes, spaces, or metacharacters cannot break
# out of the command string (especially the admin password).
q_dir=$(printf '%q' "$MC_DIR")
q_user=$(printf '%q' "$ADMIN_USER")
q_pass=$(printf '%q' "$ADMIN_PASS")

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
        curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
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
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
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
su -s /bin/bash "$MC_USER" -c "cd $q_dir && npm install --no-audit --no-fund meshcentral" >/tmp/meshcentral-npm.log 2>&1
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

print_info "Creating / syncing internal engine admin account '${ADMIN_USER}'..."
ACCOUNT_OK=0
su -s /bin/bash "$MC_USER" -c \
    "cd $q_dir && node node_modules/meshcentral --createaccount $q_user --pass $q_pass" \
    >"$VERIFY_LOG" 2>&1 && ACCOUNT_OK=1 || true
# If the account already existed from a previous run, --createaccount leaves the
# old password untouched; --resetaccount ensures the password always matches.
su -s /bin/bash "$MC_USER" -c \
    "cd $q_dir && node node_modules/meshcentral --resetaccount $q_user --pass $q_pass" \
    >>"$VERIFY_LOG" 2>&1 && ACCOUNT_OK=1 || true
ADMIN_OK=0
su -s /bin/bash "$MC_USER" -c \
    "cd $q_dir && node node_modules/meshcentral --adminaccount $q_user" \
    >>"$VERIFY_LOG" 2>&1 && ADMIN_OK=1 || true
if [ "$ACCOUNT_OK" -ne 1 ] || [ "$ADMIN_OK" -ne 1 ]; then
    print_error "Could not configure internal MeshCentral admin account '${ADMIN_USER}'."
    print_info  "See $VERIFY_LOG (secrets are redacted there)."
    exit 1
fi
print_status "Internal engine account '${ADMIN_USER}' ready"

print_info "Retrieving Login Token Key..."
TOKEN_KEY_OK=0
TOKEN_KEY_OUTPUT=$(su -s /bin/bash "$MC_USER" -c \
    "cd $q_dir && node node_modules/meshcentral --loginTokenKey" 2>>"$VERIFY_LOG") \
    && TOKEN_KEY_OK=1 || true
LOGIN_TOKEN_KEY=$(printf '%s\n' "$TOKEN_KEY_OUTPUT" | grep -Eo '[0-9a-fA-F]{64,256}' | head -n 1 || true)
if [ -n "$LOGIN_TOKEN_KEY" ]; then
    print_status "Login Token Key retrieved"
elif [ "$TOKEN_KEY_OK" -eq 1 ]; then
    print_info "Could not retrieve Login Token Key; CEM will use verified live authcookie SSO instead"
else
    print_error "Could not retrieve Login Token Key (node --loginTokenKey failed)"
    print_info  "See $VERIFY_LOG. CEM can still use live authcookie SSO, but Token Key SSO is unavailable."
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

# ─── Self-verification: live group discovery over the loopback WebSocket ─────
# A running HTTP listener alone is not proof that CEM can authenticate: a wrong
# password, a disabled account, or a missing device group all look "up" until
# the first installer click fails. Verify with meshctrl itself and fail loudly.
MESHCTRL="$MC_DIR/node_modules/meshcentral/meshctrl.js"
DEFAULT_GROUP_NAME="Client PCs"
DEFAULT_MESH_ID=""
DEFAULT_MESH_NAME=""
GROUP_COUNT=0

verify_failed() {
    print_error "$1"
    print_info "Last MeshCentral verification output (${VERIFY_LOG}):"
    tail -n 40 "$VERIFY_LOG" 2>/dev/null | sed 's/^/    /' || true
    print_info "Fix the cause above and re-run: sudo bash $0 --admin-pass '<password>'"
    exit 1
}

# meshctrl --json prints a JSON array of mesh objects with `_id` and `name`
# (sometimes after ANSI/preamble text). Also accepts meshctrl's plain CSV table
# (`"id", "name"`) as a fallback. Emit: <count>\n<matched mesh id>\n<matched name>.
# Exit 2 on unparseable non-empty output so a parse failure is NEVER mistaken
# for "zero groups" (which would create duplicate groups on every re-run).
parse_device_groups() {
python3 -c '
import csv, io, json, re, sys
raw = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", sys.stdin.read()).strip()
if not raw:
    print(0); print(""); print("")
    sys.exit(0)
groups = []
match = re.search(r"[\[{]", raw)
data = None
if match:
    try:
        data = json.loads(raw[match.start():])
    except Exception:
        data = None
if data is not None:
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                gid = str(value.get("_id") or value.get("id") or value.get("meshid") or key)
                name = str(value.get("name") or key)
            else:
                gid, name = str(key), str(value)
            groups.append((gid, name))
    elif isinstance(data, list):
        for value in data:
            if isinstance(value, dict):
                gid = str(value.get("_id") or value.get("id") or value.get("meshid") or "")
                name = str(value.get("name") or "")
            else:
                gid, name = "", str(value)
            groups.append((gid, name))
    else:
        sys.stderr.write("unexpected meshctrl group payload type\n")
        sys.exit(2)
else:
    # Fallback for meshctrl CSV table output ("id, name\n---\n\"<id>\", \"<name>\"")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if lines and lines[0].lower().replace(" ", "") == "id,name":
        for ln in lines[1:]:
            if set(ln) <= {"-"}:
                continue
            row = next(csv.reader(io.StringIO(ln), skipinitialspace=True), [])
            if len(row) >= 2 and row[0].strip():
                gid = row[0].strip()
                if not gid.startswith("mesh/"):
                    gid = f"mesh//{gid}"
                groups.append((gid, row[1].strip()))
    else:
        sys.stderr.write("unparseable meshctrl listdevicegroups output\n")
        sys.exit(2)
# Prefer the canonical installer group, otherwise fall back to the first group.
chosen = next((g for g in groups if g[1].strip().lower() == "client pcs"), None)
if chosen is None and groups:
    chosen = groups[0]
print(len(groups))
print(chosen[0] if chosen else "")
print(chosen[1] if chosen else "")
'
}

# meshctrl only supports flag-based auth; keep credentials out of the persisted
# log even if an error path echoes its argv (the file is already mode 0600).
log_meshctrl_output() {
    MC_SECRET="$ADMIN_PASS" MC_TOKEN_SECRET="$LOGIN_TOKEN_KEY" python3 -c '
import os, sys
text = sys.stdin.read()
for key in ("MC_SECRET", "MC_TOKEN_SECRET"):
    secret = os.environ.get(key, "")
    if secret:
        text = text.replace(secret, "<redacted>")
sys.stdout.write(text)
' >>"$VERIFY_LOG"
}

refresh_groups() {
    local output parsed count mid name
    output=$(node "$MESHCTRL" "${MESHCTRL_ARGS[@]}" listdevicegroups --json 2>&1) || return 1
    printf '%s\n' "$output" | log_meshctrl_output
    parsed=$(printf '%s\n' "$output" | parse_device_groups) || return 2
    count=$(printf '%s\n' "$parsed" | sed -n '1p')
    mid=$(printf '%s\n' "$parsed" | sed -n '2p')
    name=$(printf '%s\n' "$parsed" | sed -n '3p')
    [[ "$count" =~ ^[0-9]+$ ]] || return 2
    GROUP_COUNT="$count"
    DEFAULT_MESH_ID="$mid"
    DEFAULT_MESH_NAME="$name"
    return 0
}

if [ ! -f "$MESHCTRL" ]; then
    verify_failed "meshctrl.js not found at ${MESHCTRL}; cannot self-verify the engine."
fi

echo "--- MeshCentral self-verification $(date -u +%Y-%m-%dT%H:%M:%SZ) ---" >>"$VERIFY_LOG"
MESHCTRL_ARGS=(--url "ws://127.0.0.1:${MC_PORT}" --loginuser "$ADMIN_USER" --loginpass "$ADMIN_PASS")
if [ -n "$LOGIN_TOKEN_KEY" ]; then
    MESHCTRL_ARGS+=(--loginkey "$LOGIN_TOKEN_KEY")
fi

if ! refresh_groups; then
    # Some builds reject --loginkey together with a password; retry with the
    # service account credentials alone before giving up.
    MESHCTRL_ARGS=(--url "ws://127.0.0.1:${MC_PORT}" --loginuser "$ADMIN_USER" --loginpass "$ADMIN_PASS")
    refresh_groups || verify_failed \
        "meshctrl could not list device groups over ws://127.0.0.1:${MC_PORT}."
fi

if [ "$GROUP_COUNT" -eq 0 ]; then
    print_info "No device groups found — creating '${DEFAULT_GROUP_NAME}' via meshctrl..."
    group_output=$(node "$MESHCTRL" "${MESHCTRL_ARGS[@]}" adddevicegroup --name "$DEFAULT_GROUP_NAME" 2>&1) \
        || verify_failed "meshctrl could not create the '${DEFAULT_GROUP_NAME}' device group."
    printf '%s\n' "$group_output" | log_meshctrl_output
    refresh_groups || verify_failed \
        "meshctrl could not re-list device groups after creating '${DEFAULT_GROUP_NAME}'."
fi

if [ -z "$DEFAULT_MESH_ID" ]; then
    verify_failed "No usable device group after verification; expected '${DEFAULT_GROUP_NAME}'."
fi
print_status "Verified live device group '${DEFAULT_MESH_NAME}' (${DEFAULT_MESH_ID})"

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
MC_LOCAL_PORT="$MC_PORT" \
MC_DEFAULT_MESH_ID="$DEFAULT_MESH_ID" \
MC_DEFAULT_MESH_NAME="$DEFAULT_MESH_NAME" \
python3 - "$AUTOLINK_FILE" << 'PYEOF'
import json, os, sys
payload = {
    "server_url": os.environ["MC_SERVER_URL"],
    "username": os.environ["MC_ADMIN_USER"],
    "password": os.environ["MC_ADMIN_PASS"],
    "login_token_key": os.environ.get("MC_LOGIN_KEY", ""),
    "local_install_path": os.environ["MC_INSTALL_DIR"],
    "verify_ssl": os.environ.get("MC_VERIFY_SSL", "1") == "1",
    "local_port": int(os.environ.get("MC_LOCAL_PORT") or 4430),
    "default_mesh_id": os.environ.get("MC_DEFAULT_MESH_ID", ""),
    "default_mesh_name": os.environ.get("MC_DEFAULT_MESH_NAME", ""),
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
Verified Group:  ${DEFAULT_MESH_NAME} (${DEFAULT_MESH_ID})
Loopback:        ws://127.0.0.1:${MC_PORT}
Direct Access:   LOCKED — redirects straight to ${SITE_ORIGIN}/web/remote
EOF
chmod 600 "$CRED_FILE"

# ─── Ensure CEM Python deps, sync CEM DB from autolink, and restart CEM ──────
CRM_SERVICE="crm-backend"
if [ -n "${CRM_DIR:-}" ] && [ -d "$CRM_DIR" ]; then
    CRM_PY=""
    for cand in "$CRM_DIR/.venv/bin/python" "$CRM_DIR/venv/bin/python" "$(command -v python3 2>/dev/null || true)"; do
        if [ -n "$cand" ] && [ -x "$cand" ]; then
            CRM_PY="$cand"
            break
        fi
    done

    if [ -n "$CRM_PY" ]; then
        if ! "$CRM_PY" -c "import aiohttp, cryptography" >/dev/null 2>&1; then
            print_info "Installing missing MeshCentral Python dependencies (aiohttp, cryptography)..."
            "$CRM_PY" -m pip install -q aiohttp cryptography || true
            if ! "$CRM_PY" -c "import aiohttp, cryptography" >/dev/null 2>&1; then
                print_error "Python dependency verification failed (aiohttp, cryptography)."
                exit 1
            fi
        fi

        print_info "Syncing verified MeshCentral settings & default group into CEM database..."
        SYNC_SCRIPT="/tmp/cem-mesh-sync.$$.py"
        cat > "$SYNC_SCRIPT" << 'PYEOF'
import asyncio, json, os, sys
from datetime import datetime
from pathlib import Path

crm_dir = Path(sys.argv[1])
autolink_path = Path(sys.argv[2])
sys.path.insert(0, str(crm_dir))

from sqlalchemy import select
from app.core.database import async_session_factory, init_models
from app.core.meshcentral import (
    build_login_user_id,
    encrypt_secret,
    normalize_server_url,
    upsert_workspace_groups,
)
from app.models.meshcentral import MeshCentralSettings
from app.models.workspace import Workspace

data = json.loads(autolink_path.read_text(encoding="utf-8"))

async def main() -> None:
    await init_models()
    async with async_session_factory() as db:
        target_raw = (os.environ.get("CEM_MESHCENTRAL_WORKSPACE_ID") or "").strip()
        if target_raw:
            target = (
                await db.execute(select(Workspace).where(Workspace.id == int(target_raw)))
            ).scalar_one_or_none()
            if target is None:
                print("ERROR: CEM workspace %s does not exist; nothing was seeded." % target_raw)
                sys.exit(1)
            target_ids = [target.id]
        else:
            # Seed exactly one tenant by default. Sharing admin credentials with
            # every workspace would grant cross-tenant MeshCentral access.
            target = (
                await db.execute(select(Workspace).order_by(Workspace.id.asc()).limit(1))
            ).scalar_one_or_none()
            if target is None:
                print("ERROR: no CEM workspace exists; nothing was seeded.")
                sys.exit(1)
            target_ids = [target.id]
            print("NOTICE: seeding first workspace %s only; re-run with --workspace-id to target another tenant." % target.id)
        now = datetime.utcnow()
        clean_url = normalize_server_url(data["server_url"])
        username = (data.get("username") or "").strip() or None
        password = (data.get("password") or "").strip() or None
        token_key = (data.get("login_token_key") or "").strip() or None
        local_port = int(data.get("local_port") or 4430)
        def_mid = (data.get("default_mesh_id") or "").strip() or None
        def_mname = (data.get("default_mesh_name") or "").strip() or None
        verify_ssl = bool(data.get("verify_ssl", True))

        for wid in target_ids:
            row = (
                await db.execute(
                    select(MeshCentralSettings).where(MeshCentralSettings.workspace_id == wid)
                )
            ).scalar_one_or_none()
            if row is None:
                row = MeshCentralSettings(workspace_id=wid, created_at=now)
                db.add(row)
            row.enabled = True
            row.server_url = clean_url
            row.domain = ""
            row.username = username
            if password:
                row.password_encrypted = encrypt_secret(password)
            if token_key:
                row.login_token_key_encrypted = encrypt_secret(token_key)
            row.login_user_id = build_login_user_id(username, "")
            row.verify_ssl = verify_ssl
            row.local_install_path = data.get("local_install_path") or "/opt/meshcentral"
            row.local_port = local_port
            if def_mid:
                row.default_mesh_id = def_mid
                row.default_mesh_name = def_mname or "Client PCs"
            row.updated_at = now
            if def_mid:
                await upsert_workspace_groups(db, wid, {def_mid: def_mname or "Client PCs"})
        await db.commit()

asyncio.run(main())
PYEOF
        chmod 644 "$SYNC_SCRIPT"
        q_crm_dir=$(printf '%q' "$CRM_DIR")
        q_crm_py=$(printf '%q' "$CRM_PY")
        q_sync_script=$(printf '%q' "$SYNC_SCRIPT")
        q_autolink_file=$(printf '%q' "$AUTOLINK_FILE")
        q_workspace_id=$(printf '%q' "$TARGET_WORKSPACE_ID")
        SYNC_OK=0
        if [ -n "${CRM_USER:-}" ] && [ "$CRM_USER" != "root" ] && id -u "$CRM_USER" >/dev/null 2>&1; then
            su -s /bin/bash "$CRM_USER" -c \
                "cd $q_crm_dir && CEM_MESHCENTRAL_WORKSPACE_ID=$q_workspace_id MESHCENTRAL_DIR=$q_dir $q_crm_py $q_sync_script $q_crm_dir $q_autolink_file" \
                && SYNC_OK=1 || SYNC_OK=0
        else
            (cd "$CRM_DIR" && CEM_MESHCENTRAL_WORKSPACE_ID="$TARGET_WORKSPACE_ID" MESHCENTRAL_DIR="$MC_DIR" "$CRM_PY" "$SYNC_SCRIPT" "$CRM_DIR" "$AUTOLINK_FILE") \
                && SYNC_OK=1 || SYNC_OK=0
        fi
        rm -f "$SYNC_SCRIPT"
        if [ "$SYNC_OK" -eq 1 ]; then
            print_status "Synced MeshCentral settings & '${DEFAULT_MESH_NAME}' group directly into CEM database"
        else
            print_error "Could not pre-sync CEM DB from ${AUTOLINK_FILE}; manual Auto-Setup is required in CEM"
            print_info  "Open Remote Settings in the target workspace and click Auto-Setup & Fill."
        fi
    fi
fi

# Restart CEM automatically so this script is truly 1-command end-to-end
if systemctl list-unit-files 2>/dev/null | grep -q "^${CRM_SERVICE}\.service"; then
    print_info "Restarting ${CRM_SERVICE}..."
    systemctl restart "$CRM_SERVICE"
    CRM_OK=0
    for _ in $(seq 1 30); do
        if curl -fsS --max-time 3 "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
            CRM_OK=1
            break
        fi
        sleep 1
    done
    if [ "$CRM_OK" -eq 1 ]; then
        print_status "${CRM_SERVICE} restarted and healthy"
    else
        print_error "${CRM_SERVICE} did not respond on port 8000 after restart"
        journalctl -u "$CRM_SERVICE" -n 20 --no-pager || true
        exit 1
    fi
fi

echo ""
echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN}   All Done! MeshCentral + CEM Ready to Use!${NC}"
echo -e "${GREEN}=====================================================${NC}\n"
echo -e "${BLUE}Direct browser access to MeshCentral is LOCKED:${NC}"
echo -e "  Anyone visiting ${SERVER_URL} directly is sent to ${GREEN}${SITE_ORIGIN}/web/remote${NC}"
echo ""
echo -e "${BLUE}Open your website right now (zero manual settings needed):${NC}"
echo -e "  ${GREEN}${SITE_ORIGIN}/web/remote${NC}"
echo ""
echo -e "Verified Group:   ${GREEN}${DEFAULT_MESH_NAME}${NC} (${DEFAULT_MESH_ID})"
echo -e "Credentials copy: ${YELLOW}${CRED_FILE}${NC} (root-only)"
echo -e "Note: If your cloud VPS has an external firewall/security group, allow TCP port ${PUBLIC_PORT}."
echo ""
