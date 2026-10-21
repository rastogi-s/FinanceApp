#!/usr/bin/env bash
# Exercises update.sh end to end against a throwaway local registry: a first install, an update with a backup, an
# update to a version that never gets healthy (must roll back), a restore of a backup, and (with systemd and sudo)
# "Update now" from the app through financeapp-update.path. Runs in CI.
#
#   e2e/update-test.sh IMAGE      IMAGE is a locally built image of this app, e.g. financeapp:ci
set -Eeuo pipefail
IMAGE=$1
REPO=$(cd "$(dirname "$0")/.." && pwd)
REG=localhost:5000/financeapp
WORK=$(mktemp -d)
cleanup() {
  cd "$WORK" || return
  sudo -n docker compose down -v >/dev/null 2>&1 || docker compose down -v >/dev/null 2>&1
  docker rm -f update-test-registry >/dev/null 2>&1
  if [ -f /etc/systemd/system/financeapp-update.path ] && sudo -n true 2>/dev/null; then
    sudo systemctl disable --now financeapp-update.path >/dev/null 2>&1
    sudo rm -f /etc/systemd/system/financeapp-update.{service,timer,path} && sudo systemctl daemon-reload
  fi
  sudo -n rm -rf "$WORK" 2>/dev/null || rm -rf "$WORK"
}
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }
fail() {
  echo "FAIL: $*"
  exit 1
}

docker run -d --name update-test-registry -p 127.0.0.1:5000:5000 registry:2 >/dev/null
publish() { # version [broken|marked]
  local extra=""
  [ "${2:-}" = broken ] && extra='CMD ["false"]'
  # A server file that differs from the one installed, to see update.sh take it from the image.
  [ "${2:-}" = marked ] && extra=$(printf 'USER root\nRUN echo "# from %s" >> /app/server/env.example\nUSER 10001' "$1")
  printf 'FROM %s\nLABEL org.opencontainers.image.version=%s\nENV APP_VERSION=%s\n%s\n' "$IMAGE" "$1" "$1" "$extra" |
    docker build -q -t "$REG:$1" - >/dev/null
  docker push -q "$REG:$1" >/dev/null
}
latest() { docker pull -q "$REG:$1" >/dev/null && docker tag "$REG:$1" "$REG:latest" && docker push -q "$REG:latest" >/dev/null; }
health() { curl -fsS http://127.0.0.1:8080/health; }

publish 1.0.0 && publish 1.0.1 && publish 1.1.0 broken && latest 1.0.0

cp "$REPO/docker-compose.yml" "$REPO/update.sh" "$WORK/" && cd "$WORK"
age-keygen -o key.txt 2>pub.txt
cat >.env <<ENV
POSTGRES_PASSWORD=$(openssl rand -hex 16)
FINANCEAPP_IMAGE=$REG
FINANCEAPP_VERSION=latest
BACKUP_AGE_RECIPIENT=$(grep -o 'age1.*' pub.txt)
VERIFY_SIGNATURE=false
DEMO_MODE=true
ENV

step "First install"
./update.sh
health | grep -q '"version":"1.0.0"' || fail "1.0.0 is not running"
grep -qE '^ENCRYPTION_KEY=.{44}$' .env || fail "update.sh did not make an ENCRYPTION_KEY"

step "Update to the latest release, with a backup"
latest 1.0.1
docker compose exec -T postgres psql -qU finance -d finance -c "create table kept(x int); insert into kept values (42);"
./update.sh
health | grep -q '"version":"1.0.1"' || fail "1.0.1 is not running"
ls backups/finance-*-1.0.0.dump.age >/dev/null || fail "no backup of 1.0.0"
for f in backups/*.age; do
  head -c 21 "$f" | grep -q '^age-encryption.org/v1' || fail "$f is not an age-encrypted file"
done

step "A version that never gets healthy is rolled back"
if HEALTH_TIMEOUT=30 ./update.sh 1.1.0; then fail "update.sh reported success for a broken version"; fi
health | grep -q '"version":"1.0.1"' || fail "1.0.1 did not come back"
grep -q '^FINANCEAPP_VERSION=1.0.1$' .env || fail ".env does not name 1.0.1"

step "Restore a backup"
docker compose exec -T postgres psql -qU finance -d finance -c "drop table kept;"
# shellcheck disable=SC2012  # backup names are fixed-format
echo RESTORE | ./update.sh --restore "$(ls -t backups/*.age | head -1)" key.txt
[ "$(docker compose exec -T postgres psql -tAU finance -d finance -c 'select x from kept')" = 42 ] || fail "restore lost data"
health | grep -q '"status":"ok"' || fail "the app is not healthy after the restore"

step "Update now from the app: financeapp-update.path runs update.sh"
if [ -d /run/systemd/system ] && sudo -n true 2>/dev/null; then
  cp "$REPO/.env.example" env.example
  cp "$REPO"/deploy/systemd/* .
  sudo ./update.sh # nothing new to install: sets up the shared folders and the units for this folder
  systemctl is-active --quiet financeapp-update.path || fail "financeapp-update.path is not running"
  grep -q '"in_app":true' update-status/agent.json || fail "agent.json doesn't say in-app updates are on"
  [ "$(stat -c %u update-request)" = 10001 ] || fail "update-request doesn't belong to the app"
  if docker compose exec -T app sh -c ': >/app/update-status/planted' 2>/dev/null; then fail "the app can write to update-status"; fi

  publish 1.2.0 marked && latest 1.2.0
  # Exactly what the app does for "Update now": an empty file, nothing else.
  docker compose exec -T app python -c \
    "import os; os.close(os.open('/app/update-request/request', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))"
  for _ in $(seq 120); do
    if health 2>/dev/null | grep -q '"version":"1.2.0"' && grep -q '"state":"done"' update-status/status.json; then break; fi
    sleep 2
  done
  health | grep -q '"version":"1.2.0"' || {
    sudo journalctl -u financeapp-update --no-pager | tail -40
    fail "1.2.0 is not running after Update now"
  }
  grep -q '"state":"done","from":"1.0.1","to":"1.2.0"' update-status/status.json || fail "status.json: $(cat update-status/status.json)"
  sudo test ! -e update-request/request || fail "the request was left behind"
  grep -q '^# from 1.2.0$' env.example || fail "update.sh didn't take the server files from the new image"
else
  echo "Skipped: needs systemd and passwordless sudo."
fi

printf '\nupdate.sh works: install, update with backup, rollback, restore, Update now.\n'
