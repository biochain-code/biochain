#!/bin/bash
# BioChain AAECN -- fresh node install
#
# Does everything we did by hand over two days on two real servers:
# system dependencies, building liboqs (version pinned to 0.15.0 -- the
# exact version confirmed compatible with dilithium_py and with liboqs-js
# in the wallet by cross_compat_test.py), a systemd service with
# auto-restart, a firewall that does NOT expose port 8000 to the public
# internet (this was a real mistake made and fixed on the first
# production server -- see the v5.39 changelog inside biochain.py itself),
# and automated database backups.
#
# Built for a clean Ubuntu 24.04/26.04 server, run as a regular user
# with sudo rights -- NOT as root.
#
# What this script deliberately does NOT do, and why:
#   - does not create a user account or SSH keys -- that's personal;
#     every operator should already have their own securely configured
#     access before they ever get this script
#   - does not set up nginx/domain/TLS automatically -- only the operator
#     has their own domain, the script can't guess it; exact commands for
#     that manual step are printed at the end if a public web frontend
#     is wanted
#   - does not register this node in any central directory -- at this
#     stage of the project, joining the network happens by direct
#     arrangement with existing operators (see
#     BioChain_Node_Discovery_Spec_v0_1)

set -euo pipefail

BIOCHAIN_DIR="$HOME/biochain"
LIBOQS_VERSION="0.15.0"
LIBOQS_PYTHON_VERSION="0.15.0"

echo "════════════════════════════════════════════════════════════"
echo "  BioChain AAECN -- node install"
echo "════════════════════════════════════════════════════════════"
echo ""

# ── Refuse to run as root ──────────────────────────────────────────
if [ "$EUID" -eq 0 ]; then
    echo "ERROR: do not run this script as root."
    echo "Create a regular user with sudo rights and run it as that user."
    echo "(see section 1 of BioChain_Production_Deployment_Report.md if you have it)"
    exit 1
fi

if ! sudo -n true 2>/dev/null; then
    echo "This script needs sudo to install packages and configure the firewall."
    echo "Enter your sudo password when the system asks for it."
fi

# ── Step 1: system dependencies ────────────────────────────────────
echo "[1/7] Installing system dependencies..."
sudo apt update -qq
sudo apt install -y -qq \
    cmake ninja-build gcc g++ python3 python3-pip python3-dev python3-venv \
    git libssl-dev pkg-config ufw sqlite3 > /dev/null
echo "      done"

# ── Step 2: build liboqs (version pinned for compatibility) ───────
echo "[2/7] Building liboqs v${LIBOQS_VERSION} (C library, ~2-3 minutes)..."
if [ -d "$HOME/liboqs" ]; then
    echo "      ~/liboqs already exists, skipping clone"
else
    git clone --branch "${LIBOQS_VERSION}" --depth 1 \
        https://github.com/open-quantum-safe/liboqs.git "$HOME/liboqs" -q
fi
mkdir -p "$HOME/liboqs/build"
cd "$HOME/liboqs/build"
cmake -GNinja -DCMAKE_INSTALL_PREFIX=/usr/local -DBUILD_SHARED_LIBS=ON .. > /dev/null
ninja > /dev/null
sudo ninja install > /dev/null
sudo ldconfig
cd "$HOME"
echo "      done"

# ── Step 3: Python dependencies ────────────────────────────────────
echo "[3/7] Installing Python dependencies..."
pip install --break-system-packages -q \
    "liboqs-python==${LIBOQS_PYTHON_VERSION}" fastapi uvicorn dilithium-py
echo "      done"

# Confirm ML-DSA-44 is actually available
CHECK=$(python3 -c "
import warnings
warnings.filterwarnings('ignore')
import oqs
print('ML-DSA-44' in oqs.get_enabled_sig_mechanisms())
" 2>/dev/null)
if [ "$CHECK" != "True" ]; then
    echo "ERROR: ML-DSA-44 is not available after installing liboqs. Aborting."
    exit 1
fi
echo "      ML-DSA-44 confirmed available"

# ── Step 4: BioChain source code ───────────────────────────────────
echo "[4/7] Checking for BioChain source code..."
mkdir -p "$BIOCHAIN_DIR"
if [ ! -f "$BIOCHAIN_DIR/biochain.py" ]; then
    echo ""
    echo "      biochain.py not found in $BIOCHAIN_DIR"
    echo "      The project isn't publicly released yet -- get biochain.py"
    echo "      directly from a current network operator and place it here:"
    echo "        $BIOCHAIN_DIR/biochain.py"
    echo ""
    echo "      Once the file is in place, run this script again -- the"
    echo "      steps already completed (1-3) won't take extra time to redo."
    exit 0
fi
echo "      biochain.py found"

# ── Step 5: network identity -- environment-based, not file-edited ────
# v5.41 moved PEER_URLS/SELF_URL out of biochain.py entirely, into
# environment variables read at process start (BIOCHAIN_PEER_URLS,
# BIOCHAIN_SELF_URL). This whole step now just collects shell
# variables for the systemd unit built in Step 7 -- biochain.py itself
# is never edited. See MATH_SPEC.md section 12a for why SELF_URL
# matters (self-filtering during gossip) and DEFAULT_BOOTSTRAP_PEERS
# (baked into biochain.py itself as public, known-good starting
# points) for what happens if you leave PEER_URLS blank below.
echo "[5/7] Network connection setup"
echo ""
echo "      This node's OWN public address (needed for peer discovery"
echo "      to recognize and ignore mentions of itself -- e.g."
echo "      https://yourdomain.com/api). Leave blank if this node has"
echo "      no public URL yet."
read -rp "      This node's public URL: " SELF_URL_INPUT

echo ""
echo "      Which node(s) should this one connect to? Enter the full"
echo "      address (e.g. https://biochainnetwork.com/api), space-"
echo "      separated for more than one. Leave blank to use the"
echo "      built-in DEFAULT_BOOTSTRAP_PEERS (biochain.py's own public,"
echo "      known-good seed list) automatically -- SELF_URL above, if"
echo "      set, is filtered out of that list for you."
read -rp "      Peer URL(s) [blank = use defaults]: " PEER_INPUT

echo ""
echo "      CORS origin for this node's own wallet (e.g."
echo "      https://yourdomain.com). Leave blank for '*' (any origin --"
echo "      fine for local/dev use, not recommended once this server is"
echo "      public -- see biochain.py's own SECURITY log line)."
read -rp "      CORS origin: " CORS_INPUT

echo "      network identity collected (applied to the service in Step 7)"

# ── Step 6: firewall -- do NOT expose port 8000 publicly ──────────
echo "[6/7] Configuring firewall..."
sudo ufw allow OpenSSH > /dev/null
sudo ufw allow 80 > /dev/null
sudo ufw allow 443 > /dev/null
# Port 8000 (the backend itself) is deliberately NOT opened to
# "Anywhere" -- this was a real mistake on the first production server,
# found and fixed on July 10, 2026. If this node needs to accept direct
# peer requests from another server by IP, open it selectively:
#   sudo ufw allow from <OTHER_SERVER_IP> to any port 8000
echo "y" | sudo ufw enable > /dev/null
echo "      firewall active (22, 80, 443 open; 8000 stays local-only)"

# ── Step 7: systemd service + automated backups ────────────────────
echo "[7/7] Setting up auto-restart and backups..."

# Build the Environment= lines conditionally -- only for values the
# operator actually provided in Step 5, so an unset one falls back to
# biochain.py's own defaults (DEFAULT_BOOTSTRAP_PEERS, "" for SELF_URL,
# "*" for CORS) rather than a literal empty string overriding them.
ENV_LINES=""
[ -n "$SELF_URL_INPUT" ] && ENV_LINES="${ENV_LINES}Environment=BIOCHAIN_SELF_URL=${SELF_URL_INPUT}\n"
[ -n "$PEER_INPUT" ] && ENV_LINES="${ENV_LINES}Environment=BIOCHAIN_PEER_URLS=${PEER_INPUT}\n"
[ -n "$CORS_INPUT" ] && ENV_LINES="${ENV_LINES}Environment=BIOCHAIN_CORS_ORIGINS=${CORS_INPUT}\n"

sudo tee /etc/systemd/system/biochain.service > /dev/null <<EOF
[Unit]
Description=BioChain AAECN Node
After=network.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${BIOCHAIN_DIR}
$(printf '%b' "$ENV_LINES")
ExecStart=/usr/bin/python3 ${BIOCHAIN_DIR}/biochain.py
Restart=on-failure
RestartSec=5
StandardOutput=append:${BIOCHAIN_DIR}/biochain.log
StandardError=append:${BIOCHAIN_DIR}/biochain.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable biochain > /dev/null
sudo systemctl start biochain

sleep 3
if sudo systemctl is-active --quiet biochain; then
    echo "      service is running"
else
    echo "      WARNING: service failed to start, check:"
    echo "      sudo systemctl status biochain"
    echo "      cat ${BIOCHAIN_DIR}/biochain.log"
fi

# Automated backups -- reuse backup_biochain.sh if it's already there,
# otherwise create a minimal version in place
if [ ! -f "$BIOCHAIN_DIR/backup_biochain.sh" ]; then
    cat > "$BIOCHAIN_DIR/backup_biochain.sh" <<'BACKUPEOF'
#!/bin/bash
set -euo pipefail
DB_PATH="$HOME/biochain/biochain.db"
BACKUP_DIR="$HOME/biochain-backups"
KEEP_DAYS=14
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/biochain_${TIMESTAMP}.db"
mkdir -p "$BACKUP_DIR"
[ -f "$DB_PATH" ] || exit 0
sqlite3 "$DB_PATH" ".backup '${BACKUP_FILE}'"
find "$BACKUP_DIR" -name "biochain_*.db" -type f -mtime +${KEEP_DAYS} -delete
BACKUPEOF
fi
chmod +x "$BIOCHAIN_DIR/backup_biochain.sh"

(crontab -l 2>/dev/null | grep -v backup_biochain.sh; echo "0 */6 * * * ${BIOCHAIN_DIR}/backup_biochain.sh") | crontab -
echo "      automated backups configured (every 6 hours)"

# ── Summary ──────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Done"
echo "════════════════════════════════════════════════════════════"
echo ""
curl -s http://127.0.0.1:8000/verify 2>/dev/null || echo "  (could not reach /verify -- check manually)"
echo ""
echo "Check node status:"
echo "  curl -s http://127.0.0.1:8000/verify"
echo ""
echo "Logs:"
echo "  tail -f ${BIOCHAIN_DIR}/biochain.log"
echo ""
echo "If you want a public web frontend (wallet on your own domain) --"
echo "that's a separate step requiring your own domain:"
echo "  1. Point an A record for your domain at this server's IP"
echo "  2. sudo apt install -y nginx certbot python3-certbot-nginx"
echo "  3. sudo certbot --nginx -d your-domain.com"
echo "  (see section 9 of BioChain_Production_Deployment_Report.md for the full nginx config)"
echo ""
