#!/usr/bin/env bash
# Updates the Finance App on this server, safely:
#   1. checks the new version's image was signed by this repository's release workflow,
#   2. makes an encrypted backup of the database,
#   3. starts the new version and waits until /health says it's healthy and on that version,
#   4. puts the previous version back if it isn't.
#
#   ./update.sh                     update to the latest release
#   ./update.sh 1.4.2               install that exact version (to go back, or to try a pre-release like 1.5.0-beta.1)
#   ./update.sh --check             say whether an update is available; change nothing
#   ./update.sh --backup            only make a backup
#   ./update.sh --restore FILE KEY  restore a backup (KEY is the private age key kept off the server)
#
# Run it from the folder with docker-compose.yml and .env. For hands-off updates, install the systemd timer in
# deploy/systemd (see README). Needs: docker with compose, age, and cosign (unless VERIFY_SIGNATURE=false in .env).
set -Eeuo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")"

BACKUP_DIR=${BACKUP_DIR:-backups}
KEEP_BACKUPS=${KEEP_BACKUPS:-14}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-180}
SIGNER='^https://github\.com/rastogi-s/(FinanceApp|financeapp)/\.github/workflows/release\.yml@refs/(heads/main|tags/v.+)$'
ISSUER=https://token.actions.githubusercontent.com

say() { printf '%s  %s\n' "$(date '+%F %T')" "$*"; }
die() {
  say "ERROR: $*" >&2
  exit 1
}

# .env is read as data, never executed.
env_get() { { grep -E "^$1=" .env || true; } | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
env_set() {
  local tmp
  tmp=$(mktemp .env.XXXXXX)
  if grep -qE "^$1=" .env; then
    sed "s|^$1=.*|$1=$2|" .env >"$tmp"
  else
    cat .env >"$tmp" && printf '%s=%s\n' "$1" "$2" >>"$tmp"
  fi
  chmod 600 "$tmp" && mv "$tmp" .env
}

[ -f docker-compose.yml ] || die "run this next to docker-compose.yml"
[ -f .env ] || die "no .env here; copy env.example to .env and fill it in"
docker compose version >/dev/null 2>&1 || die "docker compose is not installed"
chmod 600 .env

# The master key for tokens stored in the database. Made once, here, so a first install needs no extra step.
if [ -z "$(env_get ENCRYPTION_KEY)" ]; then
  env_set ENCRYPTION_KEY "$(head -c 32 /dev/urandom | base64)"
  say "Made ENCRYPTION_KEY in .env. Copy it to your password manager now: backups don't include it, and without"
  say "it the bank connections and code-app setup in a restored backup can't be read (you'd link them again)."
fi

# .env is the one source of truth for what runs: a value exported in the shell would silently override it.
unset FINANCEAPP_IMAGE FINANCEAPP_VERSION

exec 9>.update.lock
flock -n 9 || die "another update is already running"

IMAGE=$(env_get FINANCEAPP_IMAGE)
IMAGE=${IMAGE:-ghcr.io/rastogi-s/financeapp}
APP_PORT=$(env_get APP_PORT)
APP_PORT=${APP_PORT:-8080}

image_version() { docker image inspect -f '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$1" 2>/dev/null || true; }

running_version() {
  local id
  id=$(docker compose ps -q app 2>/dev/null || true)
  if [ -n "$id" ]; then
    docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$id" 2>/dev/null || true
  fi
}

verify_signature() {
  local ref=$1 digest
  if [ "$(env_get VERIFY_SIGNATURE)" = "false" ]; then
    say "Skipping the signature check (VERIFY_SIGNATURE=false in .env)."
    return
  fi
  command -v cosign >/dev/null || die "cosign is not installed, so the image's signature can't be checked (see README)"
  digest=$(docker image inspect -f '{{ range .RepoDigests }}{{ println . }}{{ end }}' "$ref" | grep -m1 "^$IMAGE@") ||
    die "no registry digest for $ref"
  cosign verify --certificate-oidc-issuer "$ISSUER" --certificate-identity-regexp "$SIGNER" "$digest" >/dev/null 2>&1 ||
    die "$ref is NOT signed by this repository's release workflow. Not installing it."
  say "Signature checked: built and signed by the release workflow."
}

backup() {
  local recipient version file
  recipient=$(env_get BACKUP_AGE_RECIPIENT)
  [ -n "$recipient" ] || die "BACKUP_AGE_RECIPIENT is not set in .env (see env.example)"
  command -v age >/dev/null || die "age is not installed (sudo apt install age)"
  docker compose --progress quiet up -d --wait postgres >/dev/null
  version=$(running_version)
  mkdir -p "$BACKUP_DIR"
  file="$BACKUP_DIR/finance-$(date -u +%Y%m%dT%H%M%SZ)-${version:-unknown}.dump.age"
  docker compose exec -T postgres pg_dump -U finance -d finance --format=custom | age -r "$recipient" -o "$file.partial"
  [ -s "$file.partial" ] || die "the backup came out empty"
  mv "$file.partial" "$file"
  say "Backup saved: $file ($(du -h "$file" | cut -f1), encrypted)"
  # Keep the newest $KEEP_BACKUPS.
  find "$BACKUP_DIR" -maxdepth 1 -name 'finance-*.dump.age' -printf '%T@ %p\n' | sort -rn | tail -n +"$((KEEP_BACKUPS + 1))" |
    cut -d' ' -f2- | xargs -r rm --
  LAST_BACKUP=$file
}

# Waits until the app answers /health with the expected version.
healthy() {
  local want=$1 deadline=$((SECONDS + HEALTH_TIMEOUT)) body
  while [ $SECONDS -lt $deadline ]; do
    body=$(docker compose exec -T app python -c \
      "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).read().decode())" \
      2>/dev/null || true)
    if [[ $body == *'"status":"ok"'* && $body == *"\"version\":\"$want\""* ]]; then
      return 0
    fi
    sleep 3
  done
  say "Last health answer: ${body:-none}"
  return 1
}

restore() {
  local file=$1 key=$2 answer
  [ -f "$file" ] || die "no such backup: $file"
  [ -f "$key" ] || die "no such key file: $key"
  command -v age >/dev/null || die "age is not installed (sudo apt install age)"
  read -r -p "This replaces everything in the database with $file. Type RESTORE to continue: " answer
  [ "$answer" = "RESTORE" ] || die "cancelled"
  docker compose --progress quiet up -d --wait postgres >/dev/null
  docker compose stop app
  age -d -i "$key" "$file" |
    docker compose exec -T postgres pg_restore -U finance -d finance --clean --if-exists --no-owner --single-transaction
  docker compose --progress quiet up -d --wait app
  say "Restored $file."
}

case "${1:-}" in
  --backup)
    backup
    exit 0
    ;;
  --restore)
    [ $# -eq 3 ] || die "usage: ./update.sh --restore FILE KEY"
    restore "$2" "$3"
    exit 0
    ;;
  --check | "" | [0-9]*) ;;
  *) die "unknown option $1 (see the top of this file)" ;;
esac

TARGET=latest
[[ ${1:-} =~ ^[0-9] ]] && TARGET=$1
CURRENT=$(running_version)

say "Fetching $IMAGE:$TARGET"
docker pull -q "$IMAGE:$TARGET" >/dev/null || die "couldn't pull $IMAGE:$TARGET (is the server logged in to ghcr.io? see README)"
NEW=$(image_version "$IMAGE:$TARGET")
[ -n "$NEW" ] || die "$IMAGE:$TARGET has no version label"

if [ "$NEW" = "$CURRENT" ]; then
  say "Already on $CURRENT. Nothing to do."
  exit 0
fi
if [ "${1:-}" = "--check" ]; then
  say "Update available: ${CURRENT:-nothing running} -> $NEW"
  exit 0
fi

verify_signature "$IMAGE:$TARGET"
docker tag "$IMAGE:$TARGET" "$IMAGE:$NEW"

LAST_BACKUP=""
if [ -n "$CURRENT" ]; then
  backup
else
  say "Nothing running yet, so there's nothing to back up."
fi

say "Starting $NEW (was ${CURRENT:-nothing})"
env_set FINANCEAPP_VERSION "$NEW"
if docker compose --progress quiet up -d --wait --wait-timeout "$HEALTH_TIMEOUT" app && healthy "$NEW"; then
  say "Updated to $NEW. It's healthy on http://127.0.0.1:$APP_PORT"
  # Keep this version and the one before it (for going back); remove older ones.
  docker image ls "$IMAGE" --format '{{.Tag}}' | grep -vxF -e "$NEW" -e "${CURRENT:-$NEW}" -e latest -e '<none>' |
    xargs -r -I{} docker image rm "$IMAGE:{}" >/dev/null 2>&1 || true
  docker image prune -f >/dev/null
  exit 0
fi

say "$NEW didn't become healthy. Recent logs:"
docker compose logs --tail 40 app || true
if [ -z "$CURRENT" ]; then
  die "there was no previous version to go back to"
fi
say "Going back to $CURRENT"
env_set FINANCEAPP_VERSION "$CURRENT"
if ! { docker compose --progress quiet up -d --wait --wait-timeout "$HEALTH_TIMEOUT" app && healthy "$CURRENT"; }; then
  die "$CURRENT didn't come back healthy either. The backup from before the update is $LAST_BACKUP"
fi
die "update to $NEW failed and $CURRENT is running again. If $NEW changed the database, restore $LAST_BACKUP with ./update.sh --restore"
