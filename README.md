# Company Portal

Self-hosted company portal for Stone Knight: announcements, collaborative docs
(markdown pages with version history + file uploads), a kanban task board, a team
directory with onboarding checklist, and a quick-links dashboard. Multi-user with
admin-managed accounts.

FastAPI + SQLite + Jinja2. No external services; everything lives in `data/`.

## Pages

| Page | What it does |
|---|---|
| Home `/` | Announcements / news feed — running log of decisions and milestones |
| Docs `/docs` | Two areas (Working / Company): markdown pages with full version history and restore, plus file uploads (50 MB max) with search |
| Tasks `/tasks` | Kanban board (To Do / In Progress / Done) with assignees |
| Team `/team` | Employee directory + per-user onboarding checklist (admins define the items) |
| Links `/links` | Company start page of tools and resources, grouped by category |
| Users `/admin/users` | Admin only: create users (temp password shown once), reset passwords, deactivate |

## Run locally

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; on Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8100
```

Port 8100 keeps local dev clear of NetWatch (8000) and the portfolio tracker
(8010 locally), and matches the Docker/VPS port below.

Open http://127.0.0.1:8100 — the first visit redirects to `/setup` to create the
admin account. After that, add users under **Users**.

## Deploy on the Hostinger VPS

Same pattern as the portfolio tracker: Docker Compose bound to localhost, Caddy
terminating TLS in front.

```bash
# on the VPS
git clone <your-repo> company-portal   # or rsync/scp the folder
cd company-portal
docker compose up -d --build
```

The app listens on `127.0.0.1:8100` (chosen so it doesn't collide with the
portfolio tracker on 8000). Add the snippet from `Caddyfile.example` to your
Caddyfile and reload:

```bash
sudo systemctl reload caddy      # or: docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Point a DNS A record for `portal.stone-knight.com` at the VPS and Caddy will
fetch the certificate automatically. Then visit the site once to run `/setup`.

Do this promptly after deploying: until `/setup` has been completed, anyone who
finds the URL could claim the admin account.

## Configuration (docker-compose.yml)

| Env var | Default | Meaning |
|---|---|---|
| `PORTAL_NAME` | `Company Portal` | Name shown in the sidebar and titles |
| `SECURE_COOKIES` | `0` | Set `1` behind HTTPS (Caddy) so session cookies are Secure |
| `DATA_DIR` | `./data` (or `/data` in Docker) | Where the SQLite DB and uploads live |

## Backups

Everything is in `data/` (SQLite DB + `uploads/`). A nightly cron on the VPS is
enough:

```bash
tar czf /root/backups/portal-$(date +%F).tar.gz -C /path/to/company-portal data
```

## Notes

- Sessions are DB-backed cookies (30-day expiry); passwords are salted PBKDF2.
- Doc pages keep every saved version; restoring an old version is itself saved
  as a new version, so nothing is ever lost.
- Markdown is rendered server-side and not HTML-sanitized — fine for a trusted
  internal team; don't open registration to the public.
- Deactivating a user kills their sessions immediately; their docs/posts remain.
