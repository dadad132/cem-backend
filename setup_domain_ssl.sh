#!/bin/bash

###############################################################################
# CRM Backend - Domain + HTTPS setup
#
# Puts nginx in front of the app so it answers on a domain name over ports
# 80/443 instead of only http://IP:8000, then gets a free Let's Encrypt
# certificate and turns on secure session cookies.
#
# Works on AlmaLinux/RHEL/Rocky (dnf) and Ubuntu/Debian (apt).
#
# Usage:
#   sudo bash setup_domain_ssl.sh --domain crm.example.com --email you@example.com
#   sudo bash setup_domain_ssl.sh -d crm.example.com -d www.crm.example.com -e you@example.com
#   sudo bash setup_domain_ssl.sh --domain crm.example.com --no-ssl   # HTTP only for now
#
# Options:
#   -d, --domain NAME      Domain to serve (repeatable for extra names)
#   -e, --email ADDRESS    Contact address for Let's Encrypt renewal notices
#       --no-ssl           Configure the domain over plain HTTP only
#       --staging          Use the Let's Encrypt staging CA (for testing)
#       --keep-port-open   Leave port 8000 reachable from outside
#       --skip-dns-check   Continue even if the domain does not resolve here
###############################################################################

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_error()  { echo -e "${RED}[✗]${NC} $1"; }
print_info()   { echo -e "${YELLOW}[i]${NC} $1"; }

SERVICE_NAME="crm-backend"
# Read the install path from the unit file rather than assuming /opt: the
# Ubuntu installer puts it under $HOME when not run as root.
APP_DIR=$(awk -F= '/^WorkingDirectory=/{print $2}'     "/etc/systemd/system/crm-backend.service" 2>/dev/null || true)
APP_DIR="${APP_DIR:-/opt/crm-backend}"
APP_PORT=8000
NGINX_CONF="/etc/nginx/conf.d/crm-backend.conf"

DOMAINS=()
EMAIL=""
USE_SSL=1
STAGING=0
CLOSE_APP_PORT=1
SKIP_DNS=0

echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}   CRM Backend - Domain & HTTPS Setup${NC}"
echo -e "${BLUE}=========================================${NC}\n"

# ─── Arguments ───────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        -d|--domain)     DOMAINS+=("$2"); shift 2 ;;
        -e|--email)      EMAIL="$2"; shift 2 ;;
        --no-ssl)        USE_SSL=0; shift ;;
        --staging)       STAGING=1; shift ;;
        --keep-port-open) CLOSE_APP_PORT=0; shift ;;
        --skip-dns-check) SKIP_DNS=1; shift ;;
        -h|--help)       sed -n '3,26p' "$0"; exit 0 ;;
        *) print_error "Unknown option: $1"; exit 1 ;;
    esac
done

# ─── Root ────────────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    print_error "This script must run as root."
    print_info  "Try:  sudo bash $0 $*"
    exit 1
fi

# ─── Prompt for anything missing ─────────────────────────────────────────────
if [ ${#DOMAINS[@]} -eq 0 ]; then
    read -r -p "Domain name for this server (e.g. crm.example.com): " reply
    [ -n "$reply" ] && DOMAINS+=("$reply")
fi
if [ ${#DOMAINS[@]} -eq 0 ]; then
    print_error "A domain name is required"
    exit 1
fi

if [ "$USE_SSL" -eq 1 ] && [ -z "$EMAIL" ]; then
    read -r -p "Email for Let's Encrypt renewal notices (blank to skip HTTPS): " EMAIL
    if [ -z "$EMAIL" ]; then
        USE_SSL=0
        print_info "No email given - configuring HTTP only"
    fi
fi

PRIMARY_DOMAIN="${DOMAINS[0]}"
print_info "Domains: ${DOMAINS[*]}"
print_info "HTTPS:   $([ "$USE_SSL" -eq 1 ] && echo yes || echo 'no (plain HTTP)')"

# ─── Package manager ─────────────────────────────────────────────────────────
if command -v dnf >/dev/null 2>&1; then
    PKG="dnf"
elif command -v apt-get >/dev/null 2>&1; then
    PKG="apt"
else
    print_error "Neither dnf nor apt-get found - unsupported system"
    exit 1
fi
print_info "Package manager: $PKG"

# ─── The app must be running before we proxy to it ───────────────────────────
if ! curl -fsS --max-time 5 "http://127.0.0.1:${APP_PORT}/health" >/dev/null 2>&1; then
    print_error "The app is not responding on 127.0.0.1:${APP_PORT}"
    print_info  "Start it first:  systemctl start ${SERVICE_NAME}"
    print_info  "Then check:      journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
    exit 1
fi
print_status "App is responding on port ${APP_PORT}"

# ─── Install nginx (and certbot) ─────────────────────────────────────────────
print_info "Installing nginx..."
if [ "$PKG" = "dnf" ]; then
    dnf install -y nginx >/dev/null
    if [ "$USE_SSL" -eq 1 ]; then
        dnf install -y epel-release >/dev/null 2>&1 || true
        if ! dnf install -y certbot python3-certbot-nginx >/dev/null 2>&1; then
            print_error "Could not install certbot (is EPEL available?)"
            print_info  "Re-run with --no-ssl to configure the domain over HTTP only"
            exit 1
        fi
    fi
else
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y nginx >/dev/null
    if [ "$USE_SSL" -eq 1 ]; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y certbot python3-certbot-nginx >/dev/null
    fi
fi
print_status "nginx installed"

# ─── DNS sanity check ────────────────────────────────────────────────────────
# Certbot's HTTP-01 challenge only works if the domain already points here.
# Checking first turns a confusing certbot failure into a clear message.
if [ "$SKIP_DNS" -eq 0 ]; then
    SERVER_IP=$(curl -fsS --max-time 8 https://ifconfig.me 2>/dev/null || echo "")
    DOMAIN_IP=$(getent hosts "$PRIMARY_DOMAIN" 2>/dev/null | awk '{print $1; exit}' || echo "")
    if [ -z "$DOMAIN_IP" ]; then
        print_error "$PRIMARY_DOMAIN does not resolve to any address."
        print_info  "Add a DNS A record pointing it at this server${SERVER_IP:+ ($SERVER_IP)}, wait for it to"
        print_info  "propagate, then re-run. To proceed anyway: --skip-dns-check"
        [ "$USE_SSL" -eq 1 ] && exit 1
    elif [ -n "$SERVER_IP" ] && [ "$DOMAIN_IP" != "$SERVER_IP" ]; then
        print_error "$PRIMARY_DOMAIN resolves to $DOMAIN_IP but this server is $SERVER_IP"
        print_info  "HTTPS issuance will fail until the A record points here."
        print_info  "To proceed anyway: --skip-dns-check"
        [ "$USE_SSL" -eq 1 ] && exit 1
    else
        print_status "DNS: $PRIMARY_DOMAIN -> $DOMAIN_IP (matches this server)"
    fi
fi

# ─── nginx vhost ─────────────────────────────────────────────────────────────
print_info "Writing $NGINX_CONF..."
cat > "$NGINX_CONF" << EOF
# CRM Backend - reverse proxy
# Generated by setup_domain_ssl.sh. Re-running the script overwrites this file.
# certbot adds the TLS listener and redirect below when HTTPS is enabled.

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAINS[*]};

    # Attachments are capped at 10MB each in the app, but a super admin can
    # upload a whole-server backup archive here, which is far larger.
    client_max_body_size 500M;

    # Creating or restoring a backup can hold the connection open for a while
    proxy_connect_timeout 60s;
    proxy_send_timeout    300s;
    proxy_read_timeout    300s;

    access_log /var/log/nginx/crm-backend.access.log;
    error_log  /var/log/nginx/crm-backend.error.log;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;

        # Host must be forwarded or the app sees "127.0.0.1" as the site name
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host  \$host;

        proxy_redirect off;
    }
}
EOF
print_status "nginx site configured"

# ─── SELinux: let nginx open a connection to the app ─────────────────────────
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" != "Disabled" ]; then
    print_info "SELinux is $(getenforce) - allowing nginx to proxy..."
    # Without this, nginx gets "Permission denied" connecting to 127.0.0.1:8000
    setsebool -P httpd_can_network_connect 1 || true
    print_status "SELinux configured"
fi

# ─── Validate and start nginx ────────────────────────────────────────────────
print_info "Checking nginx configuration..."
if ! nginx -t 2>/tmp/nginx-test.log; then
    print_error "nginx configuration test failed:"
    sed 's/^/    /' /tmp/nginx-test.log
    exit 1
fi
print_status "nginx configuration valid"

systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx
print_status "nginx running"

# ─── Firewall: open 80 and 443 ───────────────────────────────────────────────
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    print_info "Opening ports 80 and 443 in firewalld..."
    firewall-cmd --permanent --add-service=http  >/dev/null
    firewall-cmd --permanent --add-service=https >/dev/null
    if [ "$CLOSE_APP_PORT" -eq 1 ]; then
        # nginx reaches the app over 127.0.0.1, which the firewall does not
        # filter, so closing 8000 externally does not affect it. It does stop
        # anyone reaching the site over plain HTTP and bypassing TLS.
        firewall-cmd --permanent --remove-port=${APP_PORT}/tcp >/dev/null 2>&1 || true
    fi
    firewall-cmd --reload >/dev/null
    print_status "Firewall updated"
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    print_info "Opening ports 80 and 443 in ufw..."
    ufw allow 80/tcp  >/dev/null
    ufw allow 443/tcp >/dev/null
    [ "$CLOSE_APP_PORT" -eq 1 ] && ufw delete allow ${APP_PORT}/tcp >/dev/null 2>&1 || true
    print_status "Firewall updated"
else
    print_info "No active firewall detected - skipping"
fi

# ─── Verify the domain answers over HTTP before asking for a certificate ─────
print_info "Testing http://$PRIMARY_DOMAIN ..."
if curl -fsS --max-time 10 -H "Host: $PRIMARY_DOMAIN" "http://127.0.0.1/health" >/dev/null 2>&1; then
    print_status "Domain is being served by nginx"
else
    print_error "nginx did not serve the app for Host: $PRIMARY_DOMAIN"
    print_info  "Check: tail /var/log/nginx/crm-backend.error.log"
    exit 1
fi

# ─── Certificate ─────────────────────────────────────────────────────────────
if [ "$USE_SSL" -eq 1 ]; then
    print_info "Requesting a Let's Encrypt certificate..."
    CERTBOT_ARGS=(--nginx --non-interactive --agree-tos --redirect
                  --email "$EMAIL" --keep-until-expiring)
    for d in "${DOMAINS[@]}"; do CERTBOT_ARGS+=(-d "$d"); done
    [ "$STAGING" -eq 1 ] && CERTBOT_ARGS+=(--staging)

    if certbot "${CERTBOT_ARGS[@]}"; then
        print_status "Certificate installed and HTTP redirects to HTTPS"
    else
        print_error "certbot failed - the site still works over HTTP"
        print_info  "Common causes: DNS not pointing here yet, or port 80 blocked upstream."
        print_info  "Retry with:  certbot --nginx -d $PRIMARY_DOMAIN"
        USE_SSL=0
    fi

    # Renewal timer (named differently across distributions)
    for timer in certbot-renew.timer certbot.timer snap.certbot.renew.timer; do
        if systemctl list-unit-files 2>/dev/null | grep -q "^${timer}"; then
            systemctl enable --now "$timer" >/dev/null 2>&1 || true
            print_status "Automatic renewal enabled ($timer)"
            break
        fi
    done
fi

# ─── Tell the app it is behind HTTPS ─────────────────────────────────────────
# APP_HTTPS_ONLY marks session cookies Secure. It must be true only once TLS
# actually works: set on plain HTTP, browsers refuse to send the cookie and
# every login silently fails.
if [ -f "$APP_DIR/.env" ]; then
    WANT=$([ "$USE_SSL" -eq 1 ] && echo true || echo false)
    if grep -q '^APP_HTTPS_ONLY=' "$APP_DIR/.env"; then
        sed -i "s/^APP_HTTPS_ONLY=.*/APP_HTTPS_ONLY=${WANT}/" "$APP_DIR/.env"
    else
        echo "APP_HTTPS_ONLY=${WANT}" >> "$APP_DIR/.env"
    fi
    print_status "Set APP_HTTPS_ONLY=${WANT} in .env"
fi

# ─── Forward the real client IP to the app ───────────────────────────────────
# Without --proxy-headers every request looks like it came from 127.0.0.1,
# so audit lines such as the super admin claim would record nginx, not the user.
UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
if [ -f "$UNIT" ] && ! grep -q -- "--proxy-headers" "$UNIT"; then
    print_info "Enabling proxy headers on the app service..."
    sed -i "s|\(ExecStart=.*uvicorn app.main:app.*\)|\1 --proxy-headers --forwarded-allow-ips=127.0.0.1|" "$UNIT"
    systemctl daemon-reload
    print_status "Proxy headers enabled"
fi

systemctl restart "$SERVICE_NAME"
# Startup runs migrations and starts schedulers, so give it room rather than
# assuming a fixed delay is enough.
APP_OK=0
for _ in $(seq 1 30); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${APP_PORT}/health" >/dev/null 2>&1; then
        APP_OK=1
        break
    fi
    sleep 1
done
if [ "$APP_OK" -ne 1 ]; then
    print_error "The app did not come back up after the restart"
    journalctl -u "$SERVICE_NAME" -n 20 --no-pager || true
    exit 1
fi
print_status "App restarted"

# ─── Final check ─────────────────────────────────────────────────────────────
SCHEME=$([ "$USE_SSL" -eq 1 ] && echo https || echo http)
print_info "Verifying ${SCHEME}://${PRIMARY_DOMAIN} ..."
sleep 2
if curl -fsS --max-time 15 "${SCHEME}://${PRIMARY_DOMAIN}/health" >/dev/null 2>&1; then
    print_status "Site is live at ${SCHEME}://${PRIMARY_DOMAIN}"
else
    print_error "Could not reach ${SCHEME}://${PRIMARY_DOMAIN}/health from this server"
    print_info  "It may still work from outside. Check DNS and any upstream firewall."
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   Domain setup complete${NC}"
echo -e "${GREEN}=========================================${NC}\n"

echo -e "${BLUE}Your site:${NC}"
for d in "${DOMAINS[@]}"; do
    echo -e "  ${GREEN}${SCHEME}://${d}${NC}"
done
echo ""
if [ "$USE_SSL" -eq 1 ]; then
    echo -e "${BLUE}Certificate:${NC}"
    echo -e "  Renews automatically. Test with: ${YELLOW}certbot renew --dry-run${NC}"
    echo -e "  Expiry:  ${YELLOW}certbot certificates${NC}"
    echo ""
fi
echo -e "${BLUE}Useful commands:${NC}"
echo -e "  nginx config test: ${YELLOW}nginx -t${NC}"
echo -e "  Reload nginx:      ${YELLOW}systemctl reload nginx${NC}"
echo -e "  nginx site file:   ${YELLOW}$NGINX_CONF${NC}"
echo -e "  nginx errors:      ${YELLOW}tail -f /var/log/nginx/crm-backend.error.log${NC}"
echo -e "  App logs:          ${YELLOW}journalctl -u ${SERVICE_NAME} -f${NC}"
echo ""
if [ "$CLOSE_APP_PORT" -eq 1 ]; then
    print_info "Port ${APP_PORT} is now closed externally; reach the site by domain."
    print_info "To reopen it: firewall-cmd --permanent --add-port=${APP_PORT}/tcp && firewall-cmd --reload"
fi
if [ "$USE_SSL" -eq 1 ]; then
    print_info "Session cookies are now Secure. If you ever move back to plain"
    print_info "HTTP, set APP_HTTPS_ONLY=false in $APP_DIR/.env or logins will fail."
fi
echo ""
