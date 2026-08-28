#!/bin/bash

###############################################################################
# CRM Backend - AlmaLinux / RHEL Automatic Installer
#
# Counterpart to install_ubuntu.sh, adapted for the RHEL family:
#   - dnf instead of apt
#   - Python 3.12 (or 3.11) from AppStream, since AlmaLinux 9 ships 3.9 as
#     the default python3 and this app targets 3.12 (see runtime.txt)
#   - a real virtualenv instead of --break-system-packages, because the
#     FastAPI/SQLModel stack is not packaged as RPMs
#   - firewalld instead of ufw, plus the SELinux booleans a reverse proxy needs
#
# Unlike the Ubuntu script, reinstalling here PRESERVES your data by default:
# data.db, .env, app/uploads and backups are kept.
#
# Usage:  sudo bash almalinux_install.sh
###############################################################################

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}   CRM Backend - AlmaLinux Installer${NC}"
echo -e "${BLUE}=========================================${NC}\n"

print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_error()  { echo -e "${RED}[✗]${NC} $1"; }
print_info()   { echo -e "${YELLOW}[i]${NC} $1"; }

# ─── Configuration ───────────────────────────────────────────────────────────
APP_NAME="crm-backend"
SERVICE_NAME="crm-backend"
PORT=8000
REPO_URL="https://github.com/dadad132/cem-backend.git"

# ─── Root / sudo handling ────────────────────────────────────────────────────
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    APP_DIR="/opt/$APP_NAME"
    # Prefer the human who ran sudo; otherwise use a dedicated system account
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        SERVICE_USER="$SUDO_USER"
    else
        SERVICE_USER="$APP_NAME"
    fi
else
    if ! command -v sudo >/dev/null 2>&1; then
        print_error "Not running as root and sudo is not installed."
        print_info  "Re-run as root:  su -c 'bash almalinux_install.sh'"
        exit 1
    fi
    SUDO="sudo"
    APP_DIR="/opt/$APP_NAME"
    SERVICE_USER="${USER:-$(id -un)}"
    print_info "Using sudo for privileged steps"
fi

# ─── Confirm we are on a RHEL-family system ──────────────────────────────────
if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_NAME="${NAME:-unknown}"
    OS_VERSION="${VERSION_ID:-unknown}"
    OS_MAJOR="${OS_VERSION%%.*}"
else
    print_error "Cannot read /etc/os-release - unsupported system"
    exit 1
fi

if ! command -v dnf >/dev/null 2>&1; then
    print_error "dnf not found. This installer targets AlmaLinux/RHEL/Rocky."
    print_info  "On Ubuntu/Debian use install_ubuntu.sh instead."
    exit 1
fi

print_info "Detected: $OS_NAME $OS_VERSION"
print_info "Installation directory: $APP_DIR"
print_info "Service will run as: $SERVICE_USER"

# ─── System packages ─────────────────────────────────────────────────────────
print_info "Installing system packages (this can take a minute)..."
$SUDO dnf install -y epel-release >/dev/null 2>&1 || \
    print_info "EPEL not added (not required, continuing)"

# Toolchain needed if any dependency has to build from source
$SUDO dnf install -y \
    gcc \
    git \
    curl \
    sqlite \
    libffi-devel \
    openssl-devel \
    policycoreutils-python-utils \
    >/dev/null
print_status "Base packages installed"

# ─── Pick a Python interpreter (3.11+) ───────────────────────────────────────
# AlmaLinux 9's default python3 is 3.9, which is older than this app targets.
find_python() {
    local cand ver
    for cand in python3.12 python3.11 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            ver=$("$cand" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null || echo 0)
            if [ "$ver" -ge 311 ]; then
                echo "$cand"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON_BIN="$(find_python || true)"

if [ -z "$PYTHON_BIN" ]; then
    print_info "No Python 3.11+ found - installing Python 3.12 from AppStream..."
    if $SUDO dnf install -y python3.12 python3.12-pip python3.12-devel >/dev/null 2>&1; then
        print_status "Python 3.12 installed"
    elif $SUDO dnf install -y python3.11 python3.11-pip python3.11-devel >/dev/null 2>&1; then
        print_status "Python 3.11 installed"
    else
        print_error "Could not install Python 3.11 or 3.12 from AppStream."
        print_info  "Check your subscription/repos:  dnf search python3.1"
        exit 1
    fi
    PYTHON_BIN="$(find_python || true)"
fi

if [ -z "$PYTHON_BIN" ]; then
    print_error "Still no suitable Python interpreter after installation"
    exit 1
fi

# Matching -devel headers help if a wheel has to be compiled
case "$PYTHON_BIN" in
    python3.12) $SUDO dnf install -y python3.12-devel >/dev/null 2>&1 || true ;;
    python3.11) $SUDO dnf install -y python3.11-devel >/dev/null 2>&1 || true ;;
    *)          $SUDO dnf install -y python3-devel    >/dev/null 2>&1 || true ;;
esac

print_status "Using $($PYTHON_BIN --version) at $(command -v "$PYTHON_BIN")"

# ─── Service account ─────────────────────────────────────────────────────────
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    print_info "Creating system account '$SERVICE_USER'..."
    $SUDO useradd --system --shell /sbin/nologin --home-dir "$APP_DIR" \
        --comment "CRM Backend service account" "$SERVICE_USER"
    print_status "Service account created"
fi

# ─── Running a command as the service account ────────────────────────────────
# $SUDO is empty when already root, so "$SUDO -u user ..." would try to run
# "-u" as a command. Use runuser as root, sudo -u otherwise.
if [ "$(id -u)" -eq 0 ]; then
    run_as_service_user() { runuser -u "$SERVICE_USER" -- "$@"; }
else
    run_as_service_user() { sudo -u "$SERVICE_USER" "$@"; }
fi

# ─── Preserve existing data across a reinstall ───────────────────────────────
STASH=""
if [ -d "$APP_DIR" ]; then
    print_info "Existing installation found at $APP_DIR"
    STASH="$(mktemp -d /tmp/crm-preserve-XXXXXX)"
    for item in data.db .env app/uploads backups logs; do
        if [ -e "$APP_DIR/$item" ]; then
            $SUDO mkdir -p "$STASH/$(dirname "$item")"
            $SUDO cp -a "$APP_DIR/$item" "$STASH/$item"
            print_info "  preserved: $item"
        fi
    done
    print_status "Existing data set aside (restored after update)"
fi

# ─── Fetch the application ───────────────────────────────────────────────────
if [ -d "$APP_DIR/.git" ]; then
    print_info "Updating existing repository..."
    $SUDO git -C "$APP_DIR" fetch origin
    $SUDO git -C "$APP_DIR" reset --hard origin/main
    print_status "Repository updated"
else
    print_info "Cloning repository into $APP_DIR..."
    $SUDO rm -rf "$APP_DIR"
    if ! $SUDO git clone "$REPO_URL" "$APP_DIR"; then
        print_error "git clone failed - check network access to GitHub"
        exit 1
    fi
    print_status "Repository cloned"
fi

if [ ! -f "$APP_DIR/requirements.txt" ]; then
    print_error "requirements.txt missing - the clone did not succeed"
    exit 1
fi

# ─── Restore preserved data ──────────────────────────────────────────────────
if [ -n "$STASH" ]; then
    print_info "Restoring preserved data..."
    for item in data.db .env app/uploads backups logs; do
        if [ -e "$STASH/$item" ]; then
            $SUDO mkdir -p "$APP_DIR/$(dirname "$item")"
            $SUDO cp -a "$STASH/$item" "$APP_DIR/$item"
        fi
    done
    $SUDO rm -rf "$STASH"
    print_status "Data restored"
fi

cd "$APP_DIR"

# ─── Virtual environment ─────────────────────────────────────────────────────
# A venv keeps the app's pinned versions away from the system Python that dnf
# manages, so an OS update can never swap a dependency out from under it.
print_info "Creating virtual environment..."
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    $SUDO rm -rf "$APP_DIR/.venv"
    if ! $SUDO "$PYTHON_BIN" -m venv "$APP_DIR/.venv"; then
        print_error "Failed to create the virtual environment"
        print_info  "Try: $SUDO dnf install -y ${PYTHON_BIN}-devel"
        exit 1
    fi
fi
VENV_PY="$APP_DIR/.venv/bin/python"
print_status "Virtual environment ready"

print_info "Installing Python dependencies (this is the slow part)..."
$SUDO "$VENV_PY" -m pip install --upgrade pip setuptools wheel >/dev/null
if ! $SUDO "$VENV_PY" -m pip install -r "$APP_DIR/requirements.txt"; then
    print_error "Dependency installation failed"
    print_info  "Re-run manually to see the error:"
    print_info  "  $SUDO $VENV_PY -m pip install -r $APP_DIR/requirements.txt"
    exit 1
fi
print_status "Python dependencies installed"

# ─── Application directories ─────────────────────────────────────────────────
print_info "Creating application directories..."
$SUDO mkdir -p \
    "$APP_DIR/logs" \
    "$APP_DIR/backups" \
    "$APP_DIR/updates" \
    "$APP_DIR/app/uploads/comments" \
    "$APP_DIR/app/uploads/chat_messages" \
    "$APP_DIR/app/uploads/tickets" \
    "$APP_DIR/app/uploads/branding" \
    "$APP_DIR/app/uploads/profile_pictures" \
    "$APP_DIR/app/static"
print_status "Directories created"

# ─── Configuration file ──────────────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    print_info "Creating .env file..."
    RANDOM_SECRET=$("$VENV_PY" -c "import secrets; print(secrets.token_hex(32))")
    $SUDO tee "$APP_DIR/.env" > /dev/null << EOF
# CRM Backend Configuration
APP_NAME=CRM Backend
APP_DEBUG=false
APP_HOST=0.0.0.0
APP_PORT=$PORT

# Keep false for plain HTTP; set true only once SSL/HTTPS is configured,
# otherwise browsers refuse to send the session cookie and logins fail.
APP_HTTPS_ONLY=false

CORS_ORIGINS=["*"]

DATABASE_URL=sqlite+aiosqlite:///./data.db
SECRET_KEY=${RANDOM_SECRET}
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_MINUTES=10080

UPDATE_CHECK_ENABLED=true
UPDATE_CHECK_INTERVAL=86400

EMAIL_CHECK_INTERVAL=120
EOF
    $SUDO chmod 600 "$APP_DIR/.env"
    print_status ".env created with a generated SECRET_KEY"
else
    print_info ".env already exists - left untouched"
fi

# ─── Ownership (before first run, so the DB is created as the right user) ────
print_info "Setting ownership to $SERVICE_USER..."
$SUDO chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"
$SUDO chmod 600 "$APP_DIR/.env" 2>/dev/null || true
print_status "Ownership set"

# ─── Database ────────────────────────────────────────────────────────────────
# Schema migrations run automatically inside the app on startup; this just
# creates the file up front so it is owned by the service account.
print_info "Initialising database..."
( cd "$APP_DIR" && run_as_service_user "$VENV_PY" - ) << 'PYEOF' || print_info "Database will initialise on first start"
import asyncio, os, sys
sys.path.insert(0, os.getcwd())
try:
    from app.core.database import init_models
    asyncio.run(init_models())
    print("Database initialised")
except Exception as e:
    print(f"Deferred to first start: {e}")
PYEOF
print_status "Database ready"

# ─── SELinux ─────────────────────────────────────────────────────────────────
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" != "Disabled" ]; then
    print_info "SELinux is $(getenforce) - applying contexts..."
    $SUDO restorecon -R "$APP_DIR" >/dev/null 2>&1 || true
    # Lets nginx/httpd proxy to the app if you put one in front of it later
    $SUDO setsebool -P httpd_can_network_connect 1 >/dev/null 2>&1 || true
    print_status "SELinux contexts applied"
fi

# ─── systemd service ─────────────────────────────────────────────────────────
print_info "Creating systemd service..."
$SUDO tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null << EOF
[Unit]
Description=CRM Backend Service
After=network-online.target
Wants=network-online.target

[Service]
# --proxy-headers makes the app read the real client IP from a reverse proxy
# on localhost. Harmless without one, and setting it here means re-running
# this installer cannot undo what setup_domain_ssl.sh configured.
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

if [ ! -f "/etc/systemd/system/${SERVICE_NAME}.service" ]; then
    print_error "Failed to write the systemd unit file"
    exit 1
fi
print_status "Systemd service created"

print_info "Enabling and starting service..."
$SUDO systemctl daemon-reload
$SUDO systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1
$SUDO systemctl restart "${SERVICE_NAME}"
sleep 3

if [ "$($SUDO systemctl is-active "${SERVICE_NAME}")" = "active" ]; then
    print_status "Service is running"
else
    print_error "Service failed to start"
    print_info  "Inspect it with:  journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
    $SUDO journalctl -u "${SERVICE_NAME}" -n 20 --no-pager || true
    exit 1
fi

# ─── Firewall ────────────────────────────────────────────────────────────────
if command -v firewall-cmd >/dev/null 2>&1 && $SUDO firewall-cmd --state >/dev/null 2>&1; then
    print_info "Opening port $PORT in firewalld..."
    $SUDO firewall-cmd --permanent --add-port=${PORT}/tcp >/dev/null
    $SUDO firewall-cmd --reload >/dev/null
    print_status "Firewall configured (port $PORT open)"
else
    print_info "firewalld not running - skipping firewall configuration"
fi

# ─── Health check ────────────────────────────────────────────────────────────
print_info "Checking that the app responds..."
HEALTH="down"
for _ in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
        HEALTH="up"
        break
    fi
    sleep 1
done

if [ "$HEALTH" = "up" ]; then
    print_status "Health check passed"
else
    print_error "Health check did not pass - the service is running but not responding"
    print_info  "Check:  journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$LOCAL_IP" ] && command -v ip >/dev/null 2>&1; then
    LOCAL_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')
fi
PUBLIC_IP=$(curl -fsS --max-time 5 ifconfig.me 2>/dev/null || echo "")

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}   Installation Complete!${NC}"
echo -e "${GREEN}=========================================${NC}\n"

echo -e "${BLUE}Server Information:${NC}"
echo -e "  Local Access:  ${GREEN}http://localhost:$PORT${NC}"
[ -n "$LOCAL_IP" ] && echo -e "  Local Network: ${GREEN}http://$LOCAL_IP:$PORT${NC}"
[ -n "$PUBLIC_IP" ] && echo -e "  Public Access: ${GREEN}http://$PUBLIC_IP:$PORT${NC}"
echo ""

echo -e "${BLUE}First step - claim the super admin account:${NC}"
echo -e "  ${YELLOW}http://${LOCAL_IP:-localhost}:$PORT/web/login/superadmin${NC}"
echo -e "  The first visitor to that page sets the password and becomes the"
echo -e "  server operator, so open it now - before anyone else can."
echo ""

echo -e "${BLUE}Serve it on a domain name over HTTPS:${NC}"
echo -e "  ${YELLOW}sudo bash $APP_DIR/setup_domain_ssl.sh --domain your.domain.com --email you@your.domain.com${NC}"
echo -e "  Puts nginx in front on ports 80/443 and gets a free certificate."
echo ""

echo -e "${BLUE}Useful Commands:${NC}"
echo -e "  Start service:   ${YELLOW}sudo systemctl start ${SERVICE_NAME}${NC}"
echo -e "  Stop service:    ${YELLOW}sudo systemctl stop ${SERVICE_NAME}${NC}"
echo -e "  Restart service: ${YELLOW}sudo systemctl restart ${SERVICE_NAME}${NC}"
echo -e "  Service status:  ${YELLOW}sudo systemctl status ${SERVICE_NAME}${NC}"
echo -e "  View logs:       ${YELLOW}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "  Update:          ${YELLOW}sudo bash $APP_DIR/almalinux_install.sh${NC}"
echo ""

echo -e "${BLUE}Application Directory:${NC}"
echo -e "  Location: ${YELLOW}$APP_DIR${NC}"
echo -e "  Config:   ${YELLOW}$APP_DIR/.env${NC}"
echo -e "  Database: ${YELLOW}$APP_DIR/data.db${NC}"
echo -e "  Backups:  ${YELLOW}$APP_DIR/backups${NC}"
echo -e "  Python:   ${YELLOW}$APP_DIR/.venv/bin/python${NC}"
echo ""

print_info "Re-running this script updates the code and keeps your data"
echo ""
