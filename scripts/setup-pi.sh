#!/usr/bin/env bash
#
# setup-pi.sh — factory provisioning for a GTI Router + Local Console (Epic 11).
#
# Idempotent master script. Safe to re-run: it installs system dependencies,
# the Python app, the kiosk user, systemd units (gti-router + gti-kiosk) and the
# nftables loopback hardening, then enables everything.
#
# Run on the Raspberry Pi (Raspberry Pi OS Bookworm) as root:
#     sudo ./scripts/setup-pi.sh
#
# Optional environment overrides:
#     APP_DIR=/opt/gti-router      install location of the Python app
#     KIOSK_USER=kiosk             unprivileged user that runs Chromium
#     NO_KIOSK=1                    headless build: skip cage/chromium/kiosk unit
#     NO_APT=1                      skip apt (deps already installed)
#
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
APP_DIR="${APP_DIR:-/opt/gti-router}"
STATE_DIR="/var/lib/gti-router"
ETC_DIR="/etc/gti-router"
KIOSK_USER="${KIOSK_USER:-kiosk}"
NO_KIOSK="${NO_KIOSK:-0}"
NO_APT="${NO_APT:-0}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { printf '\033[1;32m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[setup] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

[[ "$(id -u)" -eq 0 ]] || die "must run as root (use sudo)"
[[ -f "$REPO_ROOT/pyproject.toml" ]] || die "run from inside the gti-router repo"

# ── 1. System dependencies ────────────────────────────────────────────────────
step "1. System dependencies"
if [[ "$NO_APT" -eq 1 ]]; then
  log "Skipping apt (NO_APT=1)"
else
  PKGS=(
    # Pipeline + runtime
    ffmpeg
    python3 python3-venv python3-pip python3-dev
    # Build deps for psutil / systemd-python wheels
    build-essential pkg-config libsystemd-dev
    # Network hardening + diagnostics
    nftables curl jq iproute2
  )
  if [[ "$NO_KIOSK" -ne 1 ]]; then
    PKGS+=(cage chromium-browser seatd)
  fi
  log "Installing: ${PKGS[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y "${PKGS[@]}"
fi

# ── 2. Application install (idempotent rsync + venv) ──────────────────────────
step "2. Application → $APP_DIR"
mkdir -p "$APP_DIR"
if [[ "$REPO_ROOT" != "$APP_DIR" ]]; then
  log "Syncing source to $APP_DIR"
  if command -v rsync >/dev/null; then
    rsync -a --delete \
      --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
      --exclude '__pycache__' --exclude '*.pyc' \
      "$REPO_ROOT"/ "$APP_DIR"/
  else
    cp -a "$REPO_ROOT"/. "$APP_DIR"/
  fi
fi

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  log "Creating virtualenv"
  python3 -m venv "$APP_DIR/.venv"
fi
log "Installing Python package + deps (fastapi, uvicorn, …)"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet "$APP_DIR"

# ── 3. State + config directories ─────────────────────────────────────────────
step "3. State + config directories"
mkdir -p "$STATE_DIR/hls" "$STATE_DIR/console" "$ETC_DIR"
chmod 755 "$STATE_DIR"
log "Ensured $STATE_DIR/{hls,console} and $ETC_DIR"

# Secrets env file (never overwrite an existing one).
ENV_FILE="$ETC_DIR/router.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<'EOF'
# GTI Router secrets — injected as env vars (NFR9). Fill these in.
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
SUPABASE_SERVICE_ROLE_KEY=
# Claim token for the local-console QR (Story 11.4); falls back to serial if blank.
ROUTER_CLAIM_TOKEN=
EOF
  chmod 600 "$ENV_FILE"
  log "Created $ENV_FILE (0600) — EDIT IT with real secrets"
else
  log "$ENV_FILE already exists — left untouched"
fi

# router.yaml: seed the example only if no config exists anywhere.
if [[ ! -f "$ETC_DIR/router.yaml" && ! -f /boot/router.yaml && ! -f /boot/firmware/router.yaml ]]; then
  cp "$REPO_ROOT/config/router.yaml.example" "$ETC_DIR/router.yaml"
  chmod 600 "$ETC_DIR/router.yaml"
  warn "Seeded $ETC_DIR/router.yaml from the example — EDIT camera/device fields"
else
  log "router.yaml already present — left untouched"
fi

# ── 4. Kiosk user (idempotent) ────────────────────────────────────────────────
if [[ "$NO_KIOSK" -ne 1 ]]; then
  step "4. Kiosk user '$KIOSK_USER'"
  if id "$KIOSK_USER" >/dev/null 2>&1; then
    log "User '$KIOSK_USER' already exists"
  else
    useradd -m -s /usr/sbin/nologin "$KIOSK_USER"
    log "Created user '$KIOSK_USER'"
  fi
  # Grant GPU / input / seat access (re-adding is harmless).
  for grp in video render input seat; do
    getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" "$KIOSK_USER"
  done
  log "Added '$KIOSK_USER' to: video render input seat"
else
  step "4. Kiosk user — skipped (NO_KIOSK=1)"
fi

# ── 5. systemd units ──────────────────────────────────────────────────────────
step "5. systemd units"
install -m 644 "$REPO_ROOT/systemd/gti-router.service" /etc/systemd/system/gti-router.service
# Make the secrets EnvironmentFile active and optional ("-" = don't fail if absent).
sed -i 's|^# EnvironmentFile=/etc/gti-router/router.env|EnvironmentFile=-/etc/gti-router/router.env|' \
  /etc/systemd/system/gti-router.service
log "Installed gti-router.service (EnvironmentFile enabled)"

if [[ "$NO_KIOSK" -ne 1 ]]; then
  install -m 644 "$REPO_ROOT/systemd/gti-kiosk.service" /etc/systemd/system/gti-kiosk.service
  # Point the kiosk unit at the configured user if overridden.
  if [[ "$KIOSK_USER" != "kiosk" ]]; then
    sed -i "s|^User=kiosk|User=$KIOSK_USER|; s|^Group=kiosk|Group=$KIOSK_USER|" \
      /etc/systemd/system/gti-kiosk.service
  fi
  log "Installed gti-kiosk.service"
fi
systemctl daemon-reload

# ── 6. nftables loopback hardening (Story 11.12) ──────────────────────────────
step "6. nftables loopback hardening"
install -D -m 644 "$REPO_ROOT/packaging/nftables/gti-router-loopback.nft" \
  /etc/nftables.d/gti-router-loopback.nft
touch /etc/nftables.conf
if ! grep -q 'nftables.d' /etc/nftables.conf; then
  echo 'include "/etc/nftables.d/*.nft"' >> /etc/nftables.conf
  log "Added drop-in include to /etc/nftables.conf"
fi
systemctl enable --now nftables >/dev/null 2>&1 || true
nft -f /etc/nftables.d/gti-router-loopback.nft
log "Applied console port drop rule (8770 reachable on lo only)"

# ── 7. Enable services ────────────────────────────────────────────────────────
step "7. Enable services"
systemctl enable --now gti-router
log "gti-router enabled + started"

if [[ "$NO_KIOSK" -ne 1 ]]; then
  # Free VT1 for cage, switch to a graphical boot target.
  systemctl disable --now getty@tty1 >/dev/null 2>&1 || true
  systemctl enable seatd >/dev/null 2>&1 || true
  systemctl set-default graphical.target >/dev/null 2>&1 || true
  systemctl enable gti-kiosk
  if [[ -e /dev/dri/card0 ]]; then
    systemctl start gti-kiosk || warn "gti-kiosk did not start (check a screen is attached)"
    log "gti-kiosk enabled + started"
  else
    log "gti-kiosk enabled (will start when a display/DRM device is present)"
  fi
fi

# ── 8. Verify ─────────────────────────────────────────────────────────────────
step "8. Verification"
sleep 2
if [[ -x "$REPO_ROOT/scripts/verify-loopback.sh" ]]; then
  bash "$REPO_ROOT/scripts/verify-loopback.sh" || warn "loopback verification reported issues (see above)"
fi

printf '\n\033[1;32mProvisioning complete.\033[0m\n'
cat <<EOF
Next steps:
  1. Edit secrets:   sudo nano $ENV_FILE
  2. Edit config:    sudo nano $ETC_DIR/router.yaml   (cameras, serial, claim_token)
  3. Restart:        sudo systemctl restart gti-router
  4. Deploy the UI:  ./scripts/deploy-console.sh <this-pi-from-your-dev-machine>
  5. Console URL:    http://127.0.0.1:8770   (kiosk opens it automatically)
EOF
