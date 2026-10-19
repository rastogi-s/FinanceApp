# Finance App

A private, self-hosted, read-only view of the household's money: balances, holdings, spending, budget,
savings rate and a net-worth projection, with price and budget alerts sent to your phone.

It runs as one Docker image next to Postgres on a small server you own, reachable only over Tailscale.
Nothing in it can move money: every connection is read-only.

The build plan lives in the [Finance App Execution Plan](https://claude.ai/code/artifact/cf425499-0011-45e0-af13-d8e20bcbc76b).
This is Phase 0: the app shell running on sample data, the server setup, safe updates, and CI. Sign-in and
account linking come in Phase 1, before any real account is connected.

## What's in the repository

| Folder | What it is |
| --- | --- |
| `backend/` | FastAPI app (Python 3.13, uv). Serves `/api`, `/health` and the built web app, and sets the security headers. |
| `frontend/` | Next.js PWA (Tailwind, shadcn/ui), built as a static export that the backend serves. Phone first, light and dark. |
| `e2e/` | The smoke test (every page on a laptop and a phone, plus an accessibility check) and the `update.sh` test. |
| `Dockerfile`, `docker-compose.yml` | One image for the app; Compose runs it with Postgres. |
| `update.sh`, `deploy/systemd/` | Updating a server: backup, signed-image check, health check, rollback. |
| `.github/` | CI, CodeQL, Dependabot, and the release that runs on every merge to main. |

## Working on it

```bash
# API on :8000 with sample data
cd backend && uv sync && DEMO_MODE=true uv run uvicorn financeapp.main:app --reload

# Web app on :3000 (sends /api to :8000)
cd frontend && npm ci && npm run dev

# Checks CI runs
cd backend && uv run ruff check . && uv run pytest
cd frontend && npm run lint && npm run typecheck && npm run build

# The whole thing in Docker, then the smoke test
docker build -t financeapp:dev .
POSTGRES_PASSWORD=dev FINANCEAPP_IMAGE=financeapp FINANCEAPP_VERSION=dev DEMO_MODE=true docker compose up -d --wait
cd e2e && npm ci && npx playwright install chromium && node smoke.mjs   # screenshots in e2e/smoke-results/
```

Every amount on screen goes through the `Money` component, so the privacy button (the eye) can blur it.

## Releasing

Merging to main releases automatically, as in creator-crm: the next patch version is tagged, the image is built
for Intel/AMD and ARM, pushed to `ghcr.io/rastogi-s/financeapp`, signed with cosign, and a GitHub Release is
created with notes from `backend/financeapp/data/whats-new.json`. Add an entry there with a higher version for a
feature release (it also shows in the app under Settings → What's new). Put `[skip release]` in the merge commit
to merge without releasing. A tag like `v1.3.0-beta.1` builds a pre-release that servers only install when
asked for by name.

## Setting up the server

On a small Linux VM (Debian or Ubuntu):

1. Install Docker (with the Compose plugin), Tailscale, `age` and cosign
   (`sudo apt install age`; cosign from [its releases](https://github.com/sigstore/cosign/releases)).
2. Log the server in to the image registry with a GitHub token that can only read packages:
   `docker login ghcr.io -u rastogi-s` (classic token, `read:packages` scope only).
3. Put the server files in `/opt/financeapp`: `docker-compose.yml`, `update.sh`, `.env.example` and the two
   `financeapp-update.*` files from the latest GitHub Release (checked against its `SHA256SUMS.txt`).
4. `cp .env.example .env && chmod 600 .env`, then fill it in. Make the backup key **on your own computer**:
   `age-keygen -o finance-backup.key`, put the printed public key in `BACKUP_AGE_RECIPIENT`, and keep
   `finance-backup.key` in your password manager, not on the server.
5. `./update.sh`, which installs the latest release and checks it is healthy.
6. `sudo tailscale serve --bg --https=443 http://127.0.0.1:8080`, then add the `*.ts.net` name it prints to
   `ALLOWED_HOSTS` in `.env`, set `HSTS=true`, and run `docker compose up -d`.
7. Open that address on your phone and add it to the home screen.

The app listens on 127.0.0.1 only, so it can't be reached from the internet; Tailscale is the only way in.

### Updating

`./update.sh` checks that the new image was signed by this repository's release workflow, takes an encrypted
backup of the database, starts the new version, waits for `/health` to report it healthy, and goes back to the
previous version if it doesn't. `./update.sh --check` only reports whether there is an update, and
`./update.sh 1.4.2` installs a specific version, which is also how to go back.

For hands-off updates every night around 4 AM:

```bash
sudo cp /opt/financeapp/financeapp-update.* /etc/systemd/system/
sudo systemctl enable --now financeapp-update.timer
journalctl -u financeapp-update      # what happened
```

The app itself never touches Docker; it only shows what's new after an update.

### Backups

`update.sh` backs up before every update and keeps the newest 14 in `backups/`. Make one any time with
`./update.sh --backup`. Backups are encrypted to your public key, so the server can make them but can't read
them. To restore one, bring the private key over for the moment you need it:

```bash
./update.sh --restore backups/finance-20261019T040000Z-1.4.1.dump.age ~/finance-backup.key
```

## Security

- **Not on the internet.** Port 8080 is bound to 127.0.0.1 and reached through Tailscale only; Postgres has no
  published port and sits on an internal network with no route out.
- **Locked-down containers.** Non-root users, read-only filesystems, every Linux capability dropped,
  `no-new-privileges`, memory and process limits, no Docker socket.
- **Strict browser policy.** Each page's Content-Security-Policy allows only that page's own scripts (by hash),
  with no third-party scripts, fonts or analytics; plus `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, a
  locked-down `Permissions-Policy`, HSTS behind HTTPS, and host-name checking. API answers are never cached.
- **Supply chain.** Dependencies locked (uv.lock, package-lock.json), base images pinned by digest, Dependabot
  weekly, and in CI: pip-audit, npm audit, CodeQL, and Trivy scans of the image and Dockerfile. Release images
  carry an SBOM and provenance and are signed with cosign; `update.sh` refuses unsigned images.
- **No financial data in the browser cache.** The service worker caches the app itself, never `/api`.

Sign-in (passkeys, then password + TOTP), encrypted tokens, the audit log and "revoke all" arrive in Phase 1,
before any real account is linked.
