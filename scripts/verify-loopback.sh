#!/usr/bin/env bash
#
# verify-loopback.sh — assert the console mini-API is loopback-only (Story 11.12).
#
# Run ON THE PI. Checks, in order:
#   1. the listening socket is bound to 127.0.0.1 (not 0.0.0.0 / ::)
#   2. loopback access works
#   3. access via the box's own LAN IP is refused (proves it is off-network)
#
# Exit code 0 = hardened; non-zero = a problem to fix.
#
set -uo pipefail

PORT="${CONSOLE_PORT:-8770}"
fail=0
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; fail=1; }

echo "== 1. Listening socket =="
SOCK="$(ss -ltnH "sport = :$PORT" 2>/dev/null || true)"
if [[ -z "$SOCK" ]]; then
  bad "nothing is listening on :$PORT (is gti-router.service running?)"
elif grep -qE '127\.0\.0\.1:'"$PORT"'|\[::1\]:'"$PORT" <<<"$SOCK"; then
  ok "bound to loopback only: $(awk '{print $4}' <<<"$SOCK" | tr '\n' ' ')"
else
  bad "bound to a non-loopback address — FIX console.host to 127.0.0.1:"
  echo "$SOCK"
fi

echo "== 2. Loopback access =="
if curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/api/health"; then
  ok "http://127.0.0.1:$PORT/api/health responds"
else
  bad "console does not answer on loopback"
fi

echo "== 3. LAN reachability (must be refused) =="
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ -z "$LAN_IP" ]]; then
  ok "no LAN IP assigned (nothing to expose)"
else
  if curl -fsS -o /dev/null --max-time 3 "http://$LAN_IP:$PORT/api/health"; then
    bad "REACHABLE via LAN IP $LAN_IP:$PORT — console is exposed off-box!"
  else
    ok "not reachable via LAN IP $LAN_IP:$PORT (good)"
  fi
fi

echo
if [[ "$fail" -eq 0 ]]; then
  echo -e "\033[1;32mPASS — console is loopback-only.\033[0m"
else
  echo -e "\033[1;31mFAIL — see ✗ items above.\033[0m"
fi
exit "$fail"
