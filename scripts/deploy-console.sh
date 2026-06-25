#!/usr/bin/env bash
#
# deploy-console.sh — build the GTI Satélites "Consola Local" (Next.js static
# export) and deploy it to the Router's console directory on a Raspberry Pi.
#
# It runs `npm run build` in the frontend repo (which must have
# `output: 'export'` in next.config.js → produces ./out), streams the bundle to
# the Pi over SSH, and swaps it into place atomically. FastAPI's StaticFiles
# re-reads on every request, so NO service restart is needed.
#
# Usage:
#   scripts/deploy-console.sh user@raspberrypi.local
#   FRONTEND_DIR=~/code/GTI_satelites scripts/deploy-console.sh pi@192.168.1.50
#
# Configuration (flags override env vars override defaults):
#   <remote>            ssh target "user@host" (positional, or $DEPLOY_REMOTE)
#   --frontend <dir>    frontend repo dir            (env FRONTEND_DIR, default: .)
#   --path <dir>        remote console dir   (env REMOTE_PATH, /var/lib/gti-router/console)
#   --port <n>          ssh port                     (env SSH_PORT, default: 22)
#   --skip-build        deploy an existing ./out without rebuilding
#   --no-sudo           do not use sudo on the remote (dir already user-writable)
#   -h, --help          show this help
#
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
FRONTEND_DIR="${FRONTEND_DIR:-.}"
REMOTE="${DEPLOY_REMOTE:-}"
REMOTE_PATH="${REMOTE_PATH:-/var/lib/gti-router/console}"
SSH_PORT="${SSH_PORT:-22}"
SKIP_BUILD=0
USE_SUDO=1

log()  { printf '\033[1;32m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[deploy] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --frontend)   FRONTEND_DIR="$2"; shift 2 ;;
    --path)       REMOTE_PATH="$2";  shift 2 ;;
    --port)       SSH_PORT="$2";     shift 2 ;;
    --skip-build) SKIP_BUILD=1;      shift ;;
    --no-sudo)    USE_SUDO=0;        shift ;;
    -h|--help)    usage ;;
    -*)           die "unknown flag: $1" ;;
    *)            REMOTE="$1";       shift ;;
  esac
done

[[ -n "$REMOTE" ]] || die "missing ssh target. Usage: $0 user@host  (try --help)"
command -v ssh >/dev/null || die "ssh not found on PATH"

SUDO=""; [[ "$USE_SUDO" -eq 1 ]] && SUDO="sudo"
SSH=(ssh -p "$SSH_PORT" -o BatchMode=no "$REMOTE")

# ── 1. Build the static export ────────────────────────────────────────────────
OUT_DIR="$FRONTEND_DIR/out"
if [[ "$SKIP_BUILD" -eq 0 ]]; then
  [[ -f "$FRONTEND_DIR/package.json" ]] || die "no package.json in '$FRONTEND_DIR' (set --frontend)"
  log "Building frontend in $FRONTEND_DIR …"
  ( cd "$FRONTEND_DIR" && { [[ -d node_modules ]] || npm ci; } && npm run build )
else
  log "Skipping build (--skip-build)"
fi

[[ -d "$OUT_DIR" ]] || die "'$OUT_DIR' not found. Is output:'export' set in next.config.js?"
[[ -f "$OUT_DIR/index.html" ]] || die "'$OUT_DIR/index.html' missing — export looks incomplete"
log "Bundle ready: $(find "$OUT_DIR" -type f | wc -l | tr -d ' ') files"

# ── 2. Connectivity + remote staging paths ────────────────────────────────────
log "Checking SSH connectivity to $REMOTE …"
"${SSH[@]}" true || die "cannot ssh to $REMOTE"

REMOTE_TMP="${REMOTE_PATH}.tmp.$$"
REMOTE_OLD="${REMOTE_PATH}.old"
REMOTE_TGZ="/tmp/gti-console.$$.tgz"

# ── 3. Stream tarball over SSH ────────────────────────────────────────────────
log "Transferring bundle → $REMOTE:$REMOTE_PATH …"
tar -czf - -C "$OUT_DIR" . | "${SSH[@]}" "cat > '$REMOTE_TGZ'"

# ── 4. Atomic swap on the remote ──────────────────────────────────────────────
log "Swapping into place atomically …"
"${SSH[@]}" "bash -se" <<REMOTE_SCRIPT
set -euo pipefail
$SUDO rm -rf '$REMOTE_TMP'
$SUDO mkdir -p '$REMOTE_TMP'
$SUDO tar -xzf '$REMOTE_TGZ' -C '$REMOTE_TMP'
$SUDO mkdir -p "\$(dirname '$REMOTE_PATH')"
$SUDO rm -rf '$REMOTE_OLD'
if [ -e '$REMOTE_PATH' ]; then $SUDO mv '$REMOTE_PATH' '$REMOTE_OLD'; fi
$SUDO mv '$REMOTE_TMP' '$REMOTE_PATH'
$SUDO rm -rf '$REMOTE_OLD' '$REMOTE_TGZ'
echo "deployed \$($SUDO find '$REMOTE_PATH' -type f | wc -l | tr -d ' ') files to '$REMOTE_PATH'"
REMOTE_SCRIPT

log "Done. The console serves it immediately at http://127.0.0.1:${CONSOLE_PORT:-8770} on the Pi (no restart needed)."
