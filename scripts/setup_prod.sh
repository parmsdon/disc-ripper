#!/bin/bash
# First-time production setup for Discripper.
# Run as the 'ripper' user from /projects/ripper, using sudo for privileged steps.
# Every step is idempotent: safe to re-run at any time.
#
# Usage:
#   ./scripts/setup_prod.sh app     # app/DB machine (ripper)
#   ./scripts/setup_prod.sh ripper  # ripper machine (rimmer)

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_done() { echo "  [done] $*"; }
_skip() { echo "  [skip] $*"; }
_info() { echo "  [info] $*"; }
_fail() { echo ""; echo "PREFLIGHT ERROR: $*" >&2; exit 1; }
_step() { echo ""; echo "=== $* ==="; }

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
TARGET="${1:-}"
if [[ "$TARGET" != "app" && "$TARGET" != "ripper" ]]; then
    echo "Usage: $0 app|ripper" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Parse a value from config/prod.yaml using Python
# ---------------------------------------------------------------------------
_yaml() {
    python3 -c "import yaml; c=yaml.safe_load(open('config/prod.yaml')); print($1)"
}

# ---------------------------------------------------------------------------
# Preflight — fail fast with clear messages
# ---------------------------------------------------------------------------
_step "Preflight"

[[ "$(id -un)" == "ripper" ]] \
    || _fail "Must run as user 'ripper' (got: $(id -un))"
_done "running as ripper"

[[ "$PWD" == "/projects/ripper" ]] \
    || _fail "Must run from /projects/ripper (got: $PWD)"
_done "working directory is /projects/ripper"

[[ -f config/prod.yaml ]] \
    || _fail "config/prod.yaml not found — copy config/prod.yaml.example and fill it in"
_done "config/prod.yaml exists"

PLACEHOLDERS=$(grep -E 'changeme|your_.*_here|<[^>]+>' config/prod.yaml \
               | grep -v '^\s*#' || true)
[[ -z "$PLACEHOLDERS" ]] \
    || _fail "config/prod.yaml still has placeholder values:
$PLACEHOLDERS"
_done "config/prod.yaml has no placeholder values"

mountpoint -q /mnt/datastore \
    || _fail "/mnt/datastore is not a mountpoint — ensure NFS is mounted before running setup"
_done "/mnt/datastore is mounted"

if [[ "$TARGET" == "app" ]]; then
    grep -q '^\[mymovies\]' /etc/freetds.conf 2>/dev/null \
        || _fail "/etc/freetds.conf missing [mymovies] section — see DEPLOYMENT.md section 1 (My Movies sync)"
    _done "/etc/freetds.conf has [mymovies] section"
fi

if [[ "$TARGET" == "ripper" ]]; then
    while IFS= read -r dev; do
        [[ -e "$dev" ]] || _fail "Drive device $dev listed in prod.yaml does not exist"
        _done "drive device $dev exists"
    done < <(python3 -c "
import yaml
c = yaml.safe_load(open('config/prod.yaml'))
for d in c.get('drives', []):
    print(d['device'])
")
fi

echo ""
echo "All preflight checks passed."

# ---------------------------------------------------------------------------
# Read prod.yaml config values (used in app steps)
# ---------------------------------------------------------------------------
DB_NAME=$(_yaml "c['database']['name']")
DB_USER=$(_yaml "c['database']['user']")
DB_PASS=$(_yaml "c['database']['password']")

# ---------------------------------------------------------------------------
# App steps
# ---------------------------------------------------------------------------

if [[ "$TARGET" == "app" ]]; then

_step "1. DNF packages"

# Packages with stable rpm names — check with rpm -q
RPM_PACKAGES=(python3-psycopg2 python3-pyodbc freetds postgresql-server)
MISSING_PKGS=()
for pkg in "${RPM_PACKAGES[@]}"; do
    if rpm -q "$pkg" &>/dev/null; then
        _skip "$pkg already installed"
    else
        MISSING_PKGS+=("$pkg")
    fi
done
if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    sudo dnf install -y "${MISSING_PKGS[@]}"
    _done "installed: ${MISSING_PKGS[*]}"
else
    _skip "all rpm-checked packages already installed"
fi

# Packages whose rpm name varies by distro version — check with command -v
# and install the canonical package name only if the command is absent.
# (Fedora 44: nodejs22, nodejs22-npm, ffmpeg-free, flac; nginx/rsync stable)
declare -A CMD_PACKAGES=(
    [node]="nodejs"
    [npm]="npm"
    [ffmpeg]="ffmpeg"
    [flac]="flac"
    [nginx]="nginx"
    [rsync]="rsync"
)
MISSING_CMD_PKGS=()
for cmd in "${!CMD_PACKAGES[@]}"; do
    if command -v "$cmd" &>/dev/null; then
        _skip "$cmd already available"
    else
        MISSING_CMD_PKGS+=("${CMD_PACKAGES[$cmd]}")
    fi
done
if [[ ${#MISSING_CMD_PKGS[@]} -gt 0 ]]; then
    sudo dnf install -y "${MISSING_CMD_PKGS[@]}"
    _done "installed: ${MISSING_CMD_PKGS[*]}"
else
    _skip "all command-checked packages already available"
fi

# Verify ffmpeg was built with libmp3lame (required for CD MP3 320 profile).
# Capture first so grep -q exiting early doesn't send SIGPIPE to ffmpeg under pipefail.
if command -v ffmpeg &>/dev/null; then
    FFMPEG_ENCODERS=$(ffmpeg -hide_banner -encoders 2>/dev/null || true)
    if [[ "$FFMPEG_ENCODERS" == *libmp3lame* ]]; then
        _done "ffmpeg has libmp3lame encoder"
    else
        _fail "ffmpeg is present but does not include the libmp3lame encoder — the CD MP3 320 encode profile will not work. Install ffmpeg from RPM Fusion (which includes libmp3lame) rather than the ffmpeg-free package."
    fi
fi

if command -v HandBrakeCLI &>/dev/null; then
    _skip "HandBrakeCLI already installed"
else
    _info "HandBrakeCLI not found — install it manually from RPM Fusion:"
    _info "  sudo dnf install -y \\"
    _info "    https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-\$(rpm -E %fedora).noarch.rpm \\"
    _info "    https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-\$(rpm -E %fedora).noarch.rpm"
    _info "  sudo dnf install -y HandBrake"
fi

_step "2. Python venv and pip install"

if [[ -f venv/pyvenv.cfg ]]; then
    _skip "venv already exists"
else
    python3 -m venv venv --system-site-packages
    _done "created venv"
fi
source venv/bin/activate
pip install -q -r requirements.txt
_done "pip install -r requirements.txt"

_step "3. PostgreSQL"

# Fedora data directory (created by postgresql-setup --initdb)
PG_DATA=/var/lib/pgsql/data
PG_CHANGED=0

if sudo test -f "$PG_DATA/PG_VERSION"; then
    _skip "PostgreSQL data directory already initialised"
else
    sudo postgresql-setup --initdb
    _done "ran postgresql-setup --initdb"
fi

if systemctl is-enabled postgresql &>/dev/null; then
    _skip "postgresql already enabled"
else
    sudo systemctl enable postgresql
    _done "enabled postgresql"
fi
if systemctl is-active --quiet postgresql; then
    _skip "postgresql already running"
else
    sudo systemctl start postgresql
    _done "started postgresql"
fi

# Create role if absent (SQL-escape single quotes in password)
PG_ROLE=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER';" || true)
if [[ "$PG_ROLE" == *1* ]]; then
    _skip "postgres role '$DB_USER' already exists"
else
    ESCAPED_PASS=$(python3 -c \
        "import sys; print(sys.argv[1].replace(\"'\", \"''\"))" "$DB_PASS")
    sudo -u postgres psql -c \
        "CREATE ROLE \"$DB_USER\" WITH LOGIN PASSWORD '$ESCAPED_PASS';"
    _done "created postgres role '$DB_USER'"
fi

# Create database if absent
PG_DB=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME';" || true)
if [[ "$PG_DB" == *1* ]]; then
    _skip "postgres database '$DB_NAME' already exists"
else
    sudo -u postgres psql -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\";"
    _done "created postgres database '$DB_NAME'"
fi

# listen_addresses = '*' in postgresql.conf
PG_CONF="$PG_DATA/postgresql.conf"
if sudo grep -qE "^\s*listen_addresses\s*=\s*'\*'" "$PG_CONF"; then
    _skip "listen_addresses already set to '*'"
else
    sudo sed -i "s/^\(\s*listen_addresses\s*=\)/#\1/" "$PG_CONF"
    echo "listen_addresses = '*'" | sudo tee -a "$PG_CONF" > /dev/null
    _done "set listen_addresses = '*' in postgresql.conf"
    PG_CHANGED=1
fi

# pg_hba.conf line for 192.168.0.0/24 (regex so spacing differences don't create duplicates)
PG_HBA="$PG_DATA/pg_hba.conf"
HBA_LINE="host    ${DB_NAME}    ${DB_USER}    192.168.0.0/24    scram-sha-256"
if sudo grep -qE "^host\s+${DB_NAME}\s+${DB_USER}\s+192\.168\.0\.0/24\s+scram-sha-256" "$PG_HBA"; then
    _skip "pg_hba.conf line for 192.168.0.0/24 already present"
else
    echo "$HBA_LINE" | sudo tee -a "$PG_HBA" > /dev/null
    _done "added pg_hba.conf line for 192.168.0.0/24"
    PG_CHANGED=1
fi

if [[ $PG_CHANGED -eq 1 ]]; then
    sudo systemctl restart postgresql
    _done "restarted postgresql (config changed)"
fi

_step "4. Migrations and seed"

export DISCRIPPER_ENV=prod
alembic upgrade head
_done "alembic upgrade head"
python3 scripts/seed.py
_done "scripts/seed.py"

_step "5. Frontend build"

cd frontend
npm ci --silent
npm run build
cd ..
sudo mkdir -p /var/www/discripper
sudo rsync -a --delete frontend/dist/ /var/www/discripper/
_done "frontend built and deployed to /var/www/discripper"

_step "6. nginx"

NGINX_CONF=/etc/nginx/conf.d/discripper.conf
sudo cp deploy/nginx/discripper.conf "$NGINX_CONF"
_done "installed $NGINX_CONF"
sudo nginx -t
if systemctl is-enabled nginx &>/dev/null; then
    _skip "nginx already enabled"
else
    sudo systemctl enable nginx
    _done "enabled nginx"
fi
if systemctl is-active --quiet nginx; then
    sudo systemctl reload nginx
    _done "reloaded nginx"
else
    sudo systemctl start nginx
    _done "started nginx"
fi

_step "7. Systemd units"

for unit in discripper-api discripper-encoder discripper-mymovies; do
    sudo cp "deploy/systemd/${unit}.service" /etc/systemd/system/
    _done "installed ${unit}.service"
done
sudo systemctl daemon-reload
_done "daemon-reload"
for unit in discripper-api discripper-encoder discripper-mymovies; do
    if systemctl is-enabled "$unit" &>/dev/null; then
        _skip "$unit already enabled"
    else
        sudo systemctl enable "$unit"
        _done "enabled $unit"
    fi
    if systemctl is-active --quiet "$unit"; then
        _skip "$unit already running (use deploy_prod.sh to redeploy)"
    else
        sudo systemctl start "$unit"
        _done "started $unit"
    fi
done

_step "8. Sudoers rule"

SUDOERS_FILE=/etc/sudoers.d/discripper-deploy
SUDOERS_CONTENT="ripper ALL=(root) NOPASSWD: /usr/bin/systemctl restart discripper-api discripper-encoder discripper-mymovies, /usr/bin/rsync -a --delete /projects/ripper/frontend/dist/ /var/www/discripper/"
SUDOERS_TMP=$(mktemp)
echo "$SUDOERS_CONTENT" > "$SUDOERS_TMP"
sudo visudo -c -f "$SUDOERS_TMP"
if sudo test -f "$SUDOERS_FILE" && \
        [[ "$(sudo cat "$SUDOERS_FILE")" == "$SUDOERS_CONTENT" ]]; then
    _skip "$SUDOERS_FILE already up to date"
else
    sudo install -m 0440 "$SUDOERS_TMP" "$SUDOERS_FILE"
    _done "installed and validated $SUDOERS_FILE"
fi
rm -f "$SUDOERS_TMP"

_step "9. Health check"

sleep 3
ALL_OK=1
for unit in discripper-api discripper-encoder discripper-mymovies; do
    if systemctl is-active --quiet "$unit"; then
        _done "$unit is active"
    else
        echo "  [FAIL] $unit is NOT active" >&2
        ALL_OK=0
    fi
done

PING=$(curl -sf http://localhost/api/ping || true)
if [[ -n "$PING" ]]; then
    _done "curl http://localhost/api/ping → $PING"
else
    echo "  [FAIL] curl http://localhost/api/ping failed" >&2
    ALL_OK=0
fi

echo ""
if [[ $ALL_OK -eq 1 ]]; then
    echo "Setup complete."
    echo "UI available at: http://192.168.0.23/"
else
    echo "Setup complete with errors — check the output above." >&2
    exit 1
fi

fi  # end app

# ---------------------------------------------------------------------------
# Ripper steps
# ---------------------------------------------------------------------------

if [[ "$TARGET" == "ripper" ]]; then

_step "1. Python venv and pip install"

if [[ -f venv/pyvenv.cfg ]]; then
    _skip "venv already exists"
else
    python3 -m venv venv --system-site-packages
    _done "created venv"
fi
source venv/bin/activate
pip install -q -r requirements-ripper.txt
_done "pip install -r requirements-ripper.txt"

_step "2. Systemd unit"

sudo cp deploy/systemd/discripper-ripper.service /etc/systemd/system/
sudo systemctl daemon-reload
_done "installed discripper-ripper.service"
if systemctl is-enabled discripper-ripper &>/dev/null; then
    _skip "discripper-ripper already enabled"
else
    sudo systemctl enable discripper-ripper
    _done "enabled discripper-ripper"
fi
if systemctl is-active --quiet discripper-ripper; then
    _skip "discripper-ripper already running (use deploy_prod.sh to redeploy)"
else
    sudo systemctl start discripper-ripper
    _done "started discripper-ripper"
fi

_step "3. Sudoers rule"

SUDOERS_FILE=/etc/sudoers.d/discripper-deploy
SUDOERS_CONTENT="ripper ALL=(root) NOPASSWD: /usr/bin/systemctl restart discripper-ripper"
SUDOERS_TMP=$(mktemp)
echo "$SUDOERS_CONTENT" > "$SUDOERS_TMP"
sudo visudo -c -f "$SUDOERS_TMP"
if sudo test -f "$SUDOERS_FILE" && \
        [[ "$(sudo cat "$SUDOERS_FILE")" == "$SUDOERS_CONTENT" ]]; then
    _skip "$SUDOERS_FILE already up to date"
else
    sudo install -m 0440 "$SUDOERS_TMP" "$SUDOERS_FILE"
    _done "installed and validated $SUDOERS_FILE"
fi
rm -f "$SUDOERS_TMP"

_step "4. Health check"

sleep 3
if systemctl is-active --quiet discripper-ripper; then
    _done "discripper-ripper is active"
    echo ""
    sudo journalctl -u discripper-ripper -n 10 --no-pager
    echo ""
    echo "Setup complete."
else
    echo "  [FAIL] discripper-ripper is NOT active" >&2
    echo "" >&2
    sudo journalctl -u discripper-ripper -n 20 --no-pager >&2
    exit 1
fi

fi  # end ripper
