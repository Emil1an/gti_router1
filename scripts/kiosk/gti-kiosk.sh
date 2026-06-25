#!/usr/bin/env bash
#
# gti-kiosk.sh — launch Chromium in kiosk mode pointing at the local console.
#
# Run as the single command inside a Wayland kiosk compositor (cage):
#     cage -- /opt/gti-router/scripts/kiosk/gti-kiosk.sh
#
# Responsibilities (Story 11.11):
#   * wait until the console mini-API answers (AC#4 — retry until ready)
#   * launch Chromium fullscreen, no chrome/bars, touch-optimised, no cursor
#   * point at http://127.0.0.1:<port> served by FastAPI (Story 11.10)
#
set -euo pipefail

# ── Config (override via environment / the systemd unit) ──────────────────────
CONSOLE_HOST="${CONSOLE_HOST:-127.0.0.1}"
CONSOLE_PORT="${CONSOLE_PORT:-8770}"
URL="http://${CONSOLE_HOST}:${CONSOLE_PORT}"
HEALTH_URL="${URL}/api/health"
# Ephemeral profile so the kiosk never accumulates state across reboots.
PROFILE_DIR="${KIOSK_PROFILE_DIR:-/tmp/gti-kiosk-profile}"

log() { printf '[gti-kiosk] %s\n' "$*"; }

# ── Locate the Chromium binary (name differs across Pi OS releases) ───────────
CHROMIUM="$(command -v chromium-browser || command -v chromium || true)"
[[ -n "$CHROMIUM" ]] || { log "FATAL: chromium not installed (apt install chromium-browser)"; exit 1; }

# ── Wait for the console to come up (AC#4) ────────────────────────────────────
log "Waiting for console at ${HEALTH_URL} …"
attempt=0
until curl -fsS -o /dev/null --max-time 2 "$HEALTH_URL"; do
  attempt=$((attempt + 1))
  (( attempt % 15 == 0 )) && log "still waiting (${attempt}s) — is gti-router.service up?"
  sleep 1
done
log "Console is up after ${attempt}s — launching Chromium."

rm -rf "$PROFILE_DIR"; mkdir -p "$PROFILE_DIR"

# ── Launch Chromium in kiosk mode ─────────────────────────────────────────────
# --kiosk + --app : fullscreen, no tabs/omnibox/bars
# overscroll/pinch/translate disabled : no browser gestures on a touch panel
# --noerrdialogs/--disable-session-crashed-bubble : no "restore pages?" popups
exec "$CHROMIUM" \
  --kiosk \
  --app="$URL" \
  --ozone-platform=wayland \
  --enable-features=OverlayScrollbar \
  --disable-features=TranslateUI,OverscrollHistoryNavigation \
  --overscroll-history-navigation=0 \
  --disable-pinch \
  --touch-events=enabled \
  --noerrdialogs \
  --disable-infobars \
  --no-first-run \
  --fast-start \
  --disable-translate \
  --disable-session-crashed-bubble \
  --disable-component-update \
  --check-for-update-interval=31536000 \
  --autoplay-policy=no-user-gesture-required \
  --user-data-dir="$PROFILE_DIR" \
  --disk-cache-dir="$PROFILE_DIR/cache"
