# Finance App

A private, self-hosted, read-only view of the household's money: balances, holdings, spending, budget,
savings rate and a net-worth projection, with price and budget alerts sent to your phone.

It runs as one Docker image next to Postgres on a small server you own, reachable only over Tailscale.
Nothing in it can move money: every connection is read-only.

The build plan lives in the [Finance App Execution Plan](https://claude.ai/code/artifact/cf425499-0011-45e0-af13-d8e20bcbc76b).
Phase 1 is here: sign-in with passkeys (password + code app as the backup), linking accounts through Plaid
(banks, cards) and SnapTrade (brokerages) or by uploading a CSV/OFX file, a sync every few hours, transfers
between your own accounts left out of spending, and categories that learn from the two taps it takes to fix one.
Budgets, the savings rate and the projection come in Phase 2.

## What's in the repository

| Folder | What it is |
| --- | --- |
| `backend/` | FastAPI app (Python 3.13, uv). Serves `/api`, `/health` and the built web app, and sets the security headers. |
| `frontend/` | Next.js PWA (Tailwind, shadcn/ui), built as a static export that the backend serves. Phone first, light and dark. |
| `e2e/` | The smoke test (every page on a laptop and a phone, plus an accessibility check), the real-mode flow test (setup, passkey, uploads, sign-in), and the `update.sh` test. |
| `Dockerfile`, `docker-compose.yml` | One image for the app; Compose runs it with Postgres. |
| `update.sh`, `deploy/systemd/` | Updating a server: backup, signed-image check, health check, rollback. |
| `.github/` | CI, CodeQL, Dependabot, and the release that runs on every merge to main. |

## Working on it

```bash
# API on :8000 with sample data
cd backend && uv sync && DEMO_MODE=true uv run uvicorn financeapp.main:app --reload

# Web app on :3000 (sends /api to :8000)
cd frontend && npm ci && npm run dev

# Checks CI runs (backend tests need Postgres: createdb finance_test, or set TEST_DATABASE_URL)
cd backend && uv run ruff check . && uv run pytest
cd frontend && npm run lint && npm run typecheck && npm run build

# The whole thing in Docker, then the smoke test
docker build -t financeapp:dev .
POSTGRES_PASSWORD=dev FINANCEAPP_IMAGE=financeapp FINANCEAPP_VERSION=dev DEMO_MODE=true docker compose up -d --wait
cd e2e && npm ci && npx playwright install chromium && node smoke.mjs   # screenshots in e2e/smoke-results/

# The real thing, from an empty database: setup, passkey, uploads, sign-in (screenshots in e2e/flow-results/)
docker compose down -v
POSTGRES_PASSWORD=dev FINANCEAPP_IMAGE=financeapp FINANCEAPP_VERSION=dev ENCRYPTION_KEY=$(openssl rand -base64 32) \
  docker compose up -d --wait
SETUP_CODE=$(docker compose logs app | grep -o '[A-Z0-9]\{4\}-[A-Z0-9]\{4\}-[A-Z0-9]\{4\}' | tail -1)
cd e2e && SETUP_CODE=$SETUP_CODE node flow.mjs
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
2. Put the server files from the latest GitHub Release in `/opt/financeapp` and check them against its
   `SHA256SUMS.txt` (the image is public, so the server needs no registry login):

   ```bash
   sudo mkdir -p /opt/financeapp && sudo chown $USER /opt/financeapp && cd /opt/financeapp
   for f in docker-compose.yml update.sh env.example financeapp-update.service financeapp-update.timer financeapp-update.path SHA256SUMS.txt; do
     curl -fsSLO https://github.com/rastogi-s/FinanceApp/releases/latest/download/$f
   done
   sha256sum -c SHA256SUMS.txt && chmod +x update.sh
   ```

3. `cp env.example .env && chmod 600 .env`, then fill it in. Make the backup key **on your own computer**:
   `age-keygen -o finance-backup.key`, put the printed public key in `BACKUP_AGE_RECIPIENT`, and keep
   `finance-backup.key` in your password manager, not on the server.
4. `sudo ./update.sh`, which installs the latest release, checks it is healthy, and sets up "Update now" in the app.
5. `sudo tailscale serve --bg --https=443 http://127.0.0.1:8080`, then add the `*.ts.net` name it prints to
   `ALLOWED_HOSTS` in `.env`, set `HSTS=true`, and run `docker compose up -d`.
6. Open that address on your phone. It asks for the **setup code**, which the app prints in its log on first
   start: `docker compose logs app | grep "setup code"` (it changes on every restart until someone has set up,
   and expires after 24 hours). Then add a code app and a passkey, and add the address to your home screen.
7. **Copy `ENCRYPTION_KEY` from `.env` to your password manager.** `update.sh` made it on the first run.
8. Invite your partner from Settings → People: it makes a one-time link (valid 48 hours) to send them.
9. Link accounts from Settings → Accounts (fill in the Plaid and SnapTrade keys in `.env` first, then
   `docker compose up -d`), or upload a CSV or OFX file from your bank or broker.

Passkeys and the `__Host-` session cookie need HTTPS, which `tailscale serve` provides (browsers also allow them
on `http://localhost`). The address you sign in at is the passkey's home: passkeys made at
`finance.tail1234.ts.net` only work there, so pick the name before adding them.

The app listens on 127.0.0.1 only, so it can't be reached from the internet; Tailscale is the only way in.

### Updating

`./update.sh` checks that the new image was signed by this repository's release workflow, takes an encrypted
backup of the database, starts the new version, waits for `/health` to report it healthy, and goes back to the
previous version if it doesn't. `./update.sh --check` only reports whether there is an update, and
`./update.sh 1.4.2` installs a specific version, which is also how to go back.

Each update also refreshes `update.sh`, `docker-compose.yml`, `env.example` and the systemd units from inside
the image whose signature it just checked, so the server files never fall behind.

**From the app.** Settings shows when a new version is out (the app asks GitHub every few hours) with its notes,
and the owner gets an **Update now** button. Run `sudo ./update.sh` once to turn it on: it installs
`financeapp-update.path`, a systemd unit that runs `update.sh` when the app asks. The app still never touches
Docker. It can only leave an empty file in `update-request/`, which `update.sh` deletes without reading before
installing the latest signed release (or nothing), and it can only read `update-status/` to show how it went.
`UPDATE_CHECK=false` in `.env` stops the check.

**Every night around 4 AM**, hands-off:

```bash
sudo systemctl enable --now financeapp-update.timer   # sudo ./update.sh installed it
journalctl -u financeapp-update                        # what happened, for either way of updating
```

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

- **Sign-in.** Passkeys first (Face ID, Touch ID, fingerprint), password + code app as the backup. Passwords are
  hashed with Argon2id; a code works once. Five wrong tries lock the account for 15 minutes. Sessions end after
  15 idle minutes and 12 hours at most, live in an `HttpOnly`, `Secure`, `SameSite=Strict` `__Host-` cookie, and
  only a hash of the token is stored. Every change is CSRF-checked twice (a per-session token and the browser's
  `Origin`/`Sec-Fetch-Site`). Inviting, removing a person or passkey, changing the password and revoking need a
  fresh passkey or code from the last 10 minutes. Sign-in attempts are rate limited per address.
- **Alerts.** A sign-in from a new device, a lockout, or a connection that stopped syncing sends a push through
  ntfy (email if ntfy isn't set). Alerts never contain amounts.
- **Audit log.** Every sign-in, failure, link, import, invite and revoke is recorded and shown under Settings →
  Security.
- **Tokens encrypted.** Aggregator tokens and code-app secrets are encrypted with AES-256-GCM under
  `ENCRYPTION_KEY`, which lives only in `.env` (not the database, not the backups). Each value is bound to its
  column, so a ciphertext moved elsewhere won't decrypt.
- **Read-only, revocable.** Plaid gets only read products (transactions, investments, liabilities, balance); SnapTrade connections are
  made read-only. Plaid Link is the hosted page (no Plaid script runs inside the app, so the CSP stays strict).
  "Revoke all connections" removes every connection at Plaid and SnapTrade and deletes every token here.
- **AI is opt-in.** Nothing goes to an AI provider until the owner adds one under Settings → AI and picks it for a
  job. API keys are encrypted like tokens, checked before they're kept, never sent back to the browser, and changing
  one needs a fresh passkey or code. Text has Social Security, account and card numbers (all but the last 4
  digits), names, emails, phone numbers and street addresses taken out before it's sent; pictures can't be, so
  they only go to a provider with "Send pictures and scans" on (on by default only for a server on your own
  network). Requests never follow redirects. A monthly spending cap (default $5) refuses a request that could go
  over it, and every request is logged with its tokens and cost, never its content.
- **Uploads checked.** Files are capped at 5 MB and parsed on the server (the raw file is never kept); you see a
  preview before anything is saved. Spreadsheets are opened with hardened XML parsing and a zip-bomb limit, and
  pictures are re-encoded (which drops location and camera details). A file is only sent to AI after you've seen
  exactly what would go and said yes.

## Linking accounts

| Provider | For | Notes |
| --- | --- | --- |
| Plaid | Banks and cards (Chase, Amex, Ally...) | Hosted Link; `/transactions/sync` with a cursor. The free Trial plan covers a household. |
| SnapTrade | Brokerages (E*TRADE, Fidelity, Schwab, Robinhood...) | One SnapTrade user for the household; read-only connections. Covers E*TRADE, so its own API isn't needed. |
| File upload | Anything | Fidelity and E*TRADE positions CSVs, any bank's transactions CSV or Excel file, OFX/QFX. With AI set up: PDF statements, scans and screenshots. Fidelity 401(k)s, which no aggregator reaches well, go this way. |

## AI

Settings → AI takes Claude, OpenAI, Gemini, or any OpenAI-compatible server (Ollama, LM Studio, vLLM). Each job
(for now, reading uploads the built-in readers don't know) goes to the provider and model you pick there. Models
with a known price are costed exactly; for others, type in the price or the app assumes a high one so the cap stops
early. A local server is free and keeps everything at home: run Ollama with a vision model (`ollama pull
qwen2.5vl:7b`) and add `http://<that machine>:11434/v1`.

Each account has an "authoritative" switch for when the same account comes in two ways (say, an upload and a
link): only the authoritative one counts toward net worth. Enter a statement balance on any account to check the
synced number against it; the Phase 1 gate is every account within 1%.
