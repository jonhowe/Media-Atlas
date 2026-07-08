#!/usr/bin/env bash
set -uo pipefail

failures=0
warnings=0

say() {
  printf '%s\n' "$*"
}

pass() {
  say "OK: $*"
}

warn() {
  warnings=$((warnings + 1))
  say "WARN: $*"
}

fail() {
  failures=$((failures + 1))
  say "FAIL: $*"
}

env_value() {
  local name="$1"
  local file="${2:-.env}"
  if [[ ! -f "$file" ]]; then
    return 1
  fi
  awk -F= -v key="$name" '
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      value = substr($0, index($0, "=") + 1)
      gsub(/^"|"$/, "", value)
      print value
      exit
    }
  ' "$file"
}

need_command() {
  if command -v "$1" >/dev/null 2>&1; then
    pass "$1 is available"
  else
    fail "$1 is not available"
  fi
}

need_command docker

if [[ ! -f docker-compose.yml && ! -f compose.yml ]]; then
  fail "No docker-compose.yml or compose.yml found in $(pwd)"
else
  pass "Compose file found"
fi

if [[ ! -f .env ]]; then
  warn ".env not found; Compose defaults will be used where available"
else
  pass ".env found"
fi

compose_output=""
if command -v docker >/dev/null 2>&1; then
  if compose_output="$(docker compose config 2>&1)"; then
    pass "docker compose config renders successfully"
  else
    fail "docker compose config failed"
    say "$compose_output"
  fi
fi

if [[ -n "$compose_output" ]]; then
  for name in \
    MEDIA_ATLAS_AUTH_MODE \
    MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN \
    MEDIA_ATLAS_TRANSCODE_BACKUP_DIR \
    MEDIA_ATLAS_ALLOWED_ORIGINS; do
    if grep -q "$name" <<<"$compose_output"; then
      pass "$name is passed to the container"
    else
      fail "$name is missing from the rendered Compose environment"
    fi
  done
fi

media_root="$(env_value MEDIA_ATLAS_MEDIA_ROOT || true)"
if [[ -z "$media_root" ]]; then
  fail "MEDIA_ATLAS_MEDIA_ROOT is not set in .env"
elif [[ ! -e "$media_root" ]]; then
  fail "MEDIA_ATLAS_MEDIA_ROOT does not exist: $media_root"
elif [[ ! -r "$media_root" ]]; then
  fail "MEDIA_ATLAS_MEDIA_ROOT is not readable: $media_root"
else
  pass "MEDIA_ATLAS_MEDIA_ROOT exists and is readable: $media_root"
fi

auth_mode="$(env_value MEDIA_ATLAS_AUTH_MODE || true)"
ack_lan="$(env_value MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN || true)"
if [[ "${auth_mode:-single_admin}" == "disabled" && "${ack_lan:-false}" != "true" ]]; then
  fail "Auth is disabled without MEDIA_ATLAS_ACKNOWLEDGE_AUTH_DISABLED_LAN=true"
elif [[ "${auth_mode:-single_admin}" == "disabled" ]]; then
  warn "Auth is disabled and LAN acknowledgement is true; use only on a trusted LAN/VPN"
else
  pass "Auth mode is ${auth_mode:-single_admin}"
fi

if [[ "${auth_mode:-single_admin}" == "single_admin" ]]; then
  admin_password="$(env_value MEDIA_ATLAS_ADMIN_PASSWORD || true)"
  admin_hash="$(env_value MEDIA_ATLAS_ADMIN_PASSWORD_HASH || true)"
  session_secret="$(env_value MEDIA_ATLAS_SESSION_SECRET || true)"
  if [[ "$admin_password" == "change-this-password" ]]; then
    fail "MEDIA_ATLAS_ADMIN_PASSWORD is still the example placeholder"
  elif [[ -z "$admin_password" && -z "$admin_hash" ]]; then
    fail "single_admin auth requires MEDIA_ATLAS_ADMIN_PASSWORD or MEDIA_ATLAS_ADMIN_PASSWORD_HASH"
  else
    pass "single_admin password material is configured"
  fi
  if [[ -z "$session_secret" || "$session_secret" == "change-this-to-a-long-random-secret" ]]; then
    fail "MEDIA_ATLAS_SESSION_SECRET must be set to a long random value"
  else
    pass "MEDIA_ATLAS_SESSION_SECRET is set"
  fi
fi

allowed_origins="$(env_value MEDIA_ATLAS_ALLOWED_ORIGINS || true)"
if [[ -z "$allowed_origins" ]]; then
  warn "MEDIA_ATLAS_ALLOWED_ORIGINS is unset; browser API calls may fail behind a reverse proxy"
else
  pass "MEDIA_ATLAS_ALLOWED_ORIGINS is set to $allowed_origins"
fi

for dir in data reports logs transcode-staging transcode-backups; do
  if [[ -e "$dir" && ! -d "$dir" ]]; then
    fail "$dir exists but is not a directory"
  elif [[ -d "$dir" && ! -w "$dir" ]]; then
    fail "$dir is not writable"
  elif [[ -d "$dir" ]]; then
    pass "$dir is writable"
  else
    warn "$dir does not exist yet; Compose will create it when started"
  fi
done

if grep -q "/dev/dri:/dev/dri" <<<"$compose_output"; then
  if [[ -e /dev/dri/renderD128 ]]; then
    pass "/dev/dri/renderD128 exists for VAAPI"
  else
    warn "Compose requests /dev/dri, but /dev/dri/renderD128 was not found on this host"
  fi
fi

if command -v docker >/dev/null 2>&1 && docker compose ps --status running media-atlas >/dev/null 2>&1; then
  if docker compose exec -T media-atlas python - <<'PY' >/dev/null 2>&1
from app.health import diagnostics_status
data = diagnostics_status()
assert "runtime_config" in data
assert "version" in data
PY
  then
    pass "running container can generate diagnostics"
  else
    warn "running container diagnostics check failed; inspect docker compose logs -f media-atlas"
  fi
else
  warn "media-atlas container is not running; skipped in-container diagnostics"
fi

say ""
say "Doctor complete: ${failures} failure(s), ${warnings} warning(s)."
if [[ "$failures" -gt 0 ]]; then
  exit 1
fi
