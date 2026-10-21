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
# Run it from the folder with docker-compose.yml and .env. Needs: docker with compose, age, and cosign (unless
# VERIFY_SIGNATURE=false in .env).
#
# The server files (this script, docker-compose.yml, env.example and the systemd units) ship inside the signed image,
# so each update refreshes them from the image whose signature it just checked.
#
# "Update now" in the app: run as root once (sudo ./update.sh) and it installs financeapp-update.path, which runs this
# script when the app leaves a request in update-request/. That folder is the only thing the app can write to; it
# reads how the update went from update-status/, which it can't write to. The request is deleted, never read: this
# script installs the latest signed release or nothing, whoever asked.
set -Eeuo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")"

BACKUP_DIR=${BACKUP_DIR:-backups}
KEEP_BACKUPS=${KEEP_BACKUPS:-14}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-180}
REQUEST_DIR=update-request
STATUS_DIR=update-status
APP_UID=10001 # the app's user in the image (see Dockerfile)
UNITS=(financeapp-update.service financeapp-update.timer financeapp-update.path)
SIGNER='^https://github\.com/rastogi-s/(FinanceApp|financeapp)/\.github/workflows/release\.yml@refs/(heads/main|tags/v.+)$'
ISSUER=https://token.actions.githubusercontent.com

say() { printf '%s  %s\n' "$(date '+%F %T')" "$*"; }
die() {
  say "ERROR: $*" >&2
  report failed "${CURRENT:-}" "${NEW:-}"
  exit 1
}

# How the update is going, for the app (update-status/status.json). Only for updates, not --check, --backup or
# --restore. finish() reports the last word, after which nothing more is written.
REPORT=false
clean() { printf '%s' "$1" | tr -cd '0-9A-Za-z.-' | head -c 40; }
report() { # state [from] [to]
  $REPORT && [ -d "$STATUS_DIR" ] && [ -w "$STATUS_DIR" ] || return 0
  local tmp
  tmp=$(mktemp "$STATUS_DIR/.status.XXXXXX")
  printf '{"state":"%s","from":"%s","to":"%s","at":"%s"}\n' "$1" "$(clean "${2:-}")" "$(clean "${3:-}")" \
    "$(date -u +%FT%TZ)" >"$tmp"
  chmod 644 "$tmp" && mv -f "$tmp" "$STATUS_DIR/status.json"
}
finish() {
  report "$@"
  REPORT=false
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

UPDATING=false
case "${1:-}" in
  "" | [0-9]*)
    UPDATING=true
    # Take any "Update now" request first, before the lock: this run is the answer to it, and a request left behind
    # while another update holds the lock would only start this again. rm -r never follows a planted symlink.
    rm -rf -- "${REQUEST_DIR:?}/request"
    ;;
esac

exec 9>.update.lock
flock -n 9 || die "another update is already running"
if $UPDATING; then
  REPORT=true
  trap 'finish failed "${CURRENT:-}" "${NEW:-}"' ERR
fi

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

# Copies the server files out of an image without running it. Older images don't carry them; then nothing changes.
# The compose file in use before is kept as docker-compose.yml.prev, for going back.
sync_server_files() { # image
  local cid tmp f mode changed=""
  COMPOSE_CHANGED=false
  tmp=$(mktemp -d .server.XXXXXX)
  cid=$(docker create "$1")
  if ! docker cp "$cid:/app/server/." "$tmp/" >/dev/null 2>&1; then
    docker rm -f "$cid" >/dev/null
    rm -rf "$tmp"
    return 0
  fi
  docker rm -f "$cid" >/dev/null
  for f in docker-compose.yml update.sh env.example "${UNITS[@]}"; do
    if [ ! -f "$tmp/$f" ] || cmp -s "$tmp/$f" "$f"; then continue; fi
    [ "$f" = docker-compose.yml ] && [ -f "$f" ] && cp -p "$f" "$f.prev"
    mode=644
    [ "$f" = update.sh ] && mode=755
    # A new file moved into place: this script, already running, keeps reading its old copy.
    install -m "$mode" "$tmp/$f" "$f.new" && mv -f "$f.new" "$f"
    changed="$changed $f"
  done
  rm -rf "$tmp"
  [ -z "$changed" ] || say "Updated the server files from the image:$changed"
  [[ $changed == *docker-compose.yml* ]] && COMPOSE_CHANGED=true
  return 0
}

# Sets up the two folders shared with the app and, as root with systemd, the units for this folder. Says what's
# missing otherwise. Writes update-status/agent.json so the app knows whether "Update now" will be heard.
setup_in_app_updates() {
  local dir=$PWD u tmp changed=false in_app=false nightly=false
  mkdir -p "$STATUS_DIR" "$REQUEST_DIR"
  # The app reads this folder (mounted read-only) but must never write to it: it belongs to whoever runs this.
  [ ! -O "$STATUS_DIR" ] || chmod 755 "$STATUS_DIR"
  if [ "$(id -u)" = 0 ]; then
    chown "$APP_UID:$APP_UID" "$REQUEST_DIR"
    chmod 700 "$REQUEST_DIR"
    if [ -d /run/systemd/system ] && [[ $dir =~ ^[A-Za-z0-9/._-]+$ ]]; then
      for u in "${UNITS[@]}"; do
        [ -f "$u" ] || continue
        tmp=$(mktemp .unit.XXXXXX)
        sed "s|/opt/financeapp|$dir|g" "$u" >"$tmp"
        if cmp -s "$tmp" "/etc/systemd/system/$u"; then
          rm -f "$tmp"
        else
          install -m 644 "$tmp" "/etc/systemd/system/$u" && rm -f "$tmp"
          changed=true
        fi
      done
      if $changed; then
        systemctl daemon-reload
        ! systemctl is-active --quiet financeapp-update.path || systemctl restart financeapp-update.path
      fi
      if [ -f /etc/systemd/system/financeapp-update.path ] && ! systemctl is-enabled --quiet financeapp-update.path; then
        systemctl enable --now --quiet financeapp-update.path
        say "Turned on \"Update now\" in the app (financeapp-update.path)."
      fi
    fi
  fi
  if command -v systemctl >/dev/null; then
    systemctl is-enabled --quiet financeapp-update.path 2>/dev/null &&
      [ "$(stat -c %u "$REQUEST_DIR")" = "$APP_UID" ] && in_app=true
    systemctl is-enabled --quiet financeapp-update.timer 2>/dev/null && nightly=true
  fi
  [ -w "$STATUS_DIR" ] || return 0
  tmp=$(mktemp "$STATUS_DIR/.agent.XXXXXX")
  printf '{"in_app":%s,"nightly":%s}\n' "$in_app" "$nightly" >"$tmp"
  chmod 644 "$tmp" && mv -f "$tmp" "$STATUS_DIR/agent.json"
  $in_app || say "To update from the app too, run this once: sudo ./update.sh"
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
NEW=""
report checking "$CURRENT"

say "Fetching $IMAGE:$TARGET"
docker pull -q "$IMAGE:$TARGET" >/dev/null || die "couldn't pull $IMAGE:$TARGET (is the server logged in to ghcr.io? see README)"
NEW=$(image_version "$IMAGE:$TARGET")
[ -n "$NEW" ] || die "$IMAGE:$TARGET has no version label"

if [ "${1:-}" = "--check" ]; then
  if [ "$NEW" = "$CURRENT" ]; then say "Already on $CURRENT. Nothing to do."; else say "Update available: ${CURRENT:-nothing running} -> $NEW"; fi
  exit 0
fi
if [ "$NEW" = "$CURRENT" ]; then
  # Still bring the server files and the in-app updates setup up to date, from the image that's running.
  sync_server_files "$IMAGE:$NEW"
  setup_in_app_updates
  if $COMPOSE_CHANGED; then
    docker compose --progress quiet up -d --wait --wait-timeout "$HEALTH_TIMEOUT" app
    say "Restarted $CURRENT with the new docker-compose.yml."
  fi
  say "Already on $CURRENT. Nothing to do."
  finish current "$CURRENT" "$NEW"
  exit 0
fi

verify_signature "$IMAGE:$TARGET"
docker tag "$IMAGE:$TARGET" "$IMAGE:$NEW"
sync_server_files "$IMAGE:$NEW"
setup_in_app_updates
report installing "$CURRENT" "$NEW"

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
  finish "done" "$CURRENT" "$NEW"
  rm -f docker-compose.yml.prev
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
if $COMPOSE_CHANGED && [ -f docker-compose.yml.prev ]; then
  mv -f docker-compose.yml.prev docker-compose.yml
fi
if ! { docker compose --progress quiet up -d --wait --wait-timeout "$HEALTH_TIMEOUT" app && healthy "$CURRENT"; }; then
  die "$CURRENT didn't come back healthy either. The backup from before the update is $LAST_BACKUP"
fi
finish rolled_back "$CURRENT" "$NEW"
die "update to $NEW failed and $CURRENT is running again. If $NEW changed the database, restore $LAST_BACKUP with ./update.sh --restore"
