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
    git clone --branch "v${LIBOQS_VERSION}" --depth 1 \
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

# ── Step 5: PEER_URLS -- joining the existing network ─────────────
echo "[5/7] Network connection setup"
echo ""
echo "      Which node(s) should this one connect to? Enter the full"
echo "      address (e.g. https://biochainnetwork.com/api or"
echo "      http://1.2.3.4:8000). Separate multiple addresses with spaces."
echo "      Leave blank to configure PEER_URLS in biochain.py manually later."
read -rp "      Peer URL(s): " PEER_INPUT

if [ -n "$PEER_INPUT" ]; then
    PEER_LINES=""
    for url in $PEER_INPUT; do
        PEER_LINES="${PEER_LINES}    \"${url}\",\n"
    done
    python3 - "$BIOCHAIN_DIR/biochain.py" "$PEER_LINES" <<'PYEOF'
import sys, re
path, peer_lines = sys.argv[1], sys.argv[2]
src = open(path, encoding='utf-8').read()
pattern = re.compile(r'PEER_URLS = \[\n(?:.*\n)*?\]', re.MULTILINE)
new_block = f'PEER_URLS = [\n{peer_lines}]'
if not pattern.search(src):
    print("WARNING: could not find the PEER_URLS block -- set it manually")
    sys.exit(0)
src = pattern.sub(new_block, src, count=1)
open(path, 'w', encoding='utf-8').write(src)
print("      PEER_URLS updated")
PYEOF
else
    echo "      skipped -- set PEER_URLS in biochain.py manually"
fi

# ── Step 5b: SELF_URL -- this node's own public address ───────────
# Required for gossip/discovery to correctly filter out mentions of
# THIS node's own address (found live in production: without it, a
# node can end up auto-promoting itself into its own PEER_URLS the
# first time a trusted peer, correctly, lists this node among ITS
# trusted peers during gossip -- see MATH_SPEC.md section 12a).
echo ""
echo "      This node's OWN public address (needed for peer discovery"
echo "      to recognize and ignore mentions of itself -- e.g."
echo "      https://yourdomain.com/api). Leave blank to skip for now"
echo "      (discovery will still work, just without this self-check)."
read -rp "      This node's public URL: " SELF_URL_INPUT

if [ -n "$SELF_URL_INPUT" ]; then
    python3 - "$BIOCHAIN_DIR/biochain.py" "$SELF_URL_INPUT" <<'PYEOF'
import sys
path, self_url = sys.argv[1], sys.argv[2]
src = open(path, encoding='utf-8').read()
old_line = 'SELF_URL = ""'
if old_line not in src:
    print("WARNING: could not find the SELF_URL line -- set it manually")
    sys.exit(0)
src = src.replace(old_line, f'SELF_URL = "{self_url}"', 1)
open(path, 'w', encoding='utf-8').write(src)
print("      SELF_URL updated")
PYEOF
else
    echo "      skipped -- set SELF_URL in biochain.py manually if you add peers later"
fi

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

sudo tee /etc/systemd/system/biochain.service > /dev/null <<EOF
[Unit]
Description=BioChain AAECN Node
After=network.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${BIOCHAIN_DIR}
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
