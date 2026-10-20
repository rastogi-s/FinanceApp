#!/usr/bin/env bash
# Exercises update.sh end to end against a throwaway local registry: a first install, an update with a backup, an
# update to a version that never gets healthy (must roll back), and a restore of a backup. Runs in CI.
#
#   e2e/update-test.sh IMAGE      IMAGE is a locally built image of this app, e.g. financeapp:ci
set -Eeuo pipefail
IMAGE=$1
REPO=$(cd "$(dirname "$0")/.." && pwd)
REG=localhost:5000/financeapp
WORK=$(mktemp -d)
trap 'cd "$WORK" && docker compose down -v >/dev/null 2>&1; docker rm -f update-test-registry >/dev/null 2>&1; rm -rf "$WORK"' EXIT

step() { printf '\n== %s\n' "$*"; }
fail() {
  echo "FAIL: $*"
  exit 1
}

docker run -d --name update-test-registry -p 127.0.0.1:5000:5000 registry:2 >/dev/null
publish() { # version [broken]
  local extra=""
  [ "${2:-}" = broken ] && extra='CMD ["false"]'
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

printf '\nupdate.sh works: install, update with backup, rollback, restore.\n'
