# Running Home CMMS in Docker

A self-hosted CMMS for home maintenance. One container, two volumes, no external
services — no database server, no Redis, no internet connection required at runtime.

---

## Quick start

You need the source, because the image is built from it. Either clone the repo:

```bash
git clone https://github.com/Knator/Home-CMMS.git
cd Home-CMMS
```

…or, if you already have a working copy, just use it — nothing needs cloning:

```bash
cd /path/to/Home-CMMS
```

If the repository is **private**, cloning needs a credential: GitHub removed
password authentication in 2021. Use a personal access token as the password, or
an SSH key, or make the repository public.

Then:

```bash
cp .env.docker.example .env
$EDITOR .env                     # at minimum, set TZ
docker compose up -d
```

Open `http://<your-host>:8080`. On a fresh instance you land on a **setup page**
that creates the first administrator account.

> **Complete setup straight away.** Until an account exists, anyone who can reach
> the instance can claim the administrator account — this is how Immich, Home
> Assistant, Nextcloud and Gitea all behave. The page closes permanently once one
> account exists. Do not put a fresh instance on an untrusted network before
> finishing setup.
>
> Two ways to avoid that window entirely:
> * set `ADMIN_USERNAME`/`ADMIN_PASSWORD`, so the account is created before
>   anything starts listening; or
> * set `SETUP_WINDOW_MINUTES=5`, which closes the page that long after startup
>   and requires a restart to reopen — the approach Portainer takes.

The first start creates the database, generates a signing key, and applies all
migrations. It takes a few seconds.

---

## Environment variables

Everything is optional. The defaults give a working LAN install.

| Variable | Default | What it does |
|---|---|---|
| `TZ` | `UTC` | Your timezone, e.g. `America/New_York`. **Set this** — otherwise all times display as UTC. |
| `WEB_PORT` | `8080` | Host port to publish on. |
| `SECRET_KEY` | *generated* | Signs session cookies. Left unset, one is generated on first run and stored in the instance volume. Set it only if you would rather manage it yourself, or if that volume is not persistent. |
| `FLASK_ENV` | *unset* | Set to `production` **only when served over HTTPS**. See the warning below. |
| `TRUST_PROXY_HEADERS` | *off* | Set to `1` **only** when a reverse proxy you control sits in front. See below. |
| `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | *unset* | Optional unattended first admin. Prefer creating the account interactively so the password never sits in a file. |
| `SETUP_WINDOW_MINUTES` | `0` (no limit) | Closes the first-run setup page this many minutes after startup. Restart to reopen. |
| `GUNICORN_TIMEOUT` | `120` | Seconds before a request is killed. The default is generous because a 50 MB upload on a slow link must not be cut off. |
| `DATABASE_URL` | `sqlite:///instance/home_cmms.db` | Rarely worth changing. |
| `UPLOAD_FOLDER` | `/app/uploads` | Where attachments live inside the container. |

### Two variables that will lock you out if set wrongly

**`FLASK_ENV=production`** marks session cookies `Secure`. A `Secure` cookie is
never sent over plain `http://`, so if you set this without HTTPS in front, you
will reach the login page, submit correct credentials, and be returned to the
login page with no explanation. Set it *only* once TLS terminates in front of the
container.

**`TRUST_PROXY_HEADERS=1`** makes the app believe the `X-Forwarded-For` header.
That is correct behind nginx, Caddy or Traefik — and dangerous without one,
because any client can then set that header freely, forge its address in the
sign-in audit log, and sidestep the rate limit by changing it each attempt.

Conversely, if you *are* behind a proxy and leave this off, every request appears
to come from the proxy's address. One person failing to sign in repeatedly would
then lock out everybody at once.

---

## What lives where

Two volumes. **Both must persist.**

### `/app/instance` — small, critical

| File | Purpose |
|---|---|
| `home_cmms.db` | The entire database: assets, work orders, PMs, job plans, users. |
| `home_cmms.db-wal`, `-shm` | SQLite write-ahead log. Recent writes may live here rather than in the main file — which matters for backups, see below. |
| `secret_key` | Generated signing key, mode `0600`. Losing it signs everybody out; it does not lose data. |
| `backups/` | Archives created from the Maintenance page. |

### `/app/uploads` — large

Attachments and asset photos, filed as `<entity-type>/<id>/<uuid>_<filename>`,
plus a `.thumbnails/` cache that is regenerated on demand and never needs backing
up.

This volume dwarfs the database — a handful of phone photos is easily 50 MB
against a database of a few hundred KB.

---

## Setup recommendations

**Run one worker.** The compose file does. SQLite allows a single writer, and the
PM scheduler runs inside the app — a second worker would generate duplicate work
orders. One worker is ample for a household.

**Set `TZ`.** Otherwise every timestamp reads as UTC. Confirm it under
*Admin → Maintenance → System*, which shows the timezone in use.

**Create the first admin on the setup page**, which is the simplest route and
keeps the password out of files and `docker inspect` output. If you would rather
not have an open setup window at all, use `ADMIN_USERNAME`/`ADMIN_PASSWORD`, or
create the account from the command line before exposing the port:

```bash
docker compose exec cmms python create_admin.py
```

**Keep it on your LAN unless you have a reason not to.** If you do expose it,
put TLS in front, set `FLASK_ENV=production` and `TRUST_PROXY_HEADERS=1`, and
read the security notes at the end.

**Check for updates deliberately.** There is no auto-update. Pull, rebuild, and
restart — migrations run automatically on start.

```bash
git pull && docker compose up -d --build
```

---

## Backups

### What to back up

The `instance` volume and the `uploads` volume. Skip `uploads/.thumbnails` — it
rebuilds itself.

### The one rule

**Never copy `home_cmms.db` with `cp`, `tar` or a volume snapshot while the app
is running.** SQLite runs in WAL mode, so recent writes live in
`home_cmms.db-wal`. A plain file copy misses them and produces a database that
*opens fine* and is quietly out of date — the worst kind of bad backup.

Use one of these instead.

### Option 1 — the Maintenance page (easiest)

*Admin → Maintenance → Backups → Create backup* writes a single `.tar.gz`
containing a consistent database snapshot plus every uploaded file. Download it
somewhere else; a backup sitting inside the same volume protects you from
nothing. Set **Keep** to prune old archives automatically.

### Option 2 — from the host, scriptable

```bash
# Consistent database snapshot, safe while running
docker compose exec -T cmms python -c \
  "import sqlite3; sqlite3.connect('instance/home_cmms.db').execute('VACUUM INTO ?', ('instance/backups/db-snapshot.db',))"

# Copy both volumes out
docker compose cp cmms:/app/instance ./backup-instance
docker compose cp cmms:/app/uploads  ./backup-uploads
```

### Option 3 — stop first, then anything

With the container stopped, any copy method is safe:

```bash
docker compose stop
docker run --rm -v home-cmms_cmms-instance:/i -v home-cmms_cmms-uploads:/u \
  -v "$PWD":/out alpine tar czf /out/home-cmms-backup.tar.gz /i /u
docker compose start
```

### Restoring

Restoring is deliberately manual — overwriting a live database from a web form
is too easy to do by accident.

```bash
docker compose down

# Extract an archive made by the Maintenance page
mkdir restore && tar -xzf home-cmms-backup-YYYYMMDD-HHMMSS.tar.gz -C restore

# Put the database back, discarding any stale WAL sidecars
docker run --rm -v home-cmms_cmms-instance:/i -v "$PWD/restore":/r alpine sh -c \
  "cp /r/home_cmms.db /i/home_cmms.db && rm -f /i/home_cmms.db-wal /i/home_cmms.db-shm"

# And the attachments
docker run --rm -v home-cmms_cmms-uploads:/u -v "$PWD/restore":/r alpine sh -c \
  "cp -r /r/uploads/. /u/"

docker compose up -d
```

Migrations run on start, so a backup from an older version is brought up to date
automatically. Going *backwards* — restoring a new backup into an older image —
is not supported.

**Test your restore before you need it.** Restore into a scratch stack and log
in. An untested backup is a hope, not a backup.

### What a restore does not bring back

If `secret_key` is not in the backup, a new one is generated and everyone is
signed out once. Data is unaffected.

---

## Common operations

```bash
docker compose logs -f cmms                       # follow logs
docker compose exec cmms python create_admin.py   # add an admin
docker compose exec cmms flask db upgrade         # migrations (also run on start)
docker compose restart cmms                       # restart
docker compose down                               # stop, volumes kept
docker compose down -v                            # stop AND DELETE ALL DATA
```

`docker compose down -v` removes the volumes. That is your database and every
attachment. There is no undo.

---

## Behind a reverse proxy

Terminate TLS at the proxy, forward to the container, and set both
`FLASK_ENV=production` and `TRUST_PROXY_HEADERS=1`. Caddy example:

```
cmms.example.com {
    reverse_proxy localhost:8080
}
```

Caddy sets `X-Forwarded-For` and `X-Forwarded-Proto` itself, which is what those
two settings depend on.

---

## Security notes

The container runs as an unprivileged user (uid 10001), with
`no-new-privileges`, and writes only to its two volumes.

Built in already: sign-in rate limiting with lockout, hashed passwords, hashed
API tokens, CSRF protection on every form, an upload allowlist, and a signing key
that is generated per install rather than shipped.

Worth knowing before exposing it to the internet:

- **Everyone signed in can see and edit everything.** The `admin` role only gates
  user management and the Maintenance page. Only hand out accounts to people you
  trust with all of the data.
- **The API documentation at `/api/v1/docs` is public** — the shape of the API,
  never any data. Add `@login_required` to the two view functions in
  `app/api/routes.py` if you would rather it were not.
- **`FLASK_DEBUG` is refused** on any non-loopback host. The debugger executes
  arbitrary code; the app will not start in that configuration.
- Passwords require 8 characters and nothing else. If this is internet-facing,
  choose better ones than that implies.

---

## Troubleshooting

**Can't sign in, no error shown.** Almost always `FLASK_ENV=production` without
HTTPS. Unset it and restart.

**Everyone is locked out at once.** You are behind a proxy without
`TRUST_PROXY_HEADERS=1`, so every request shares the proxy's address. Set it, or
clear the lockouts under *Admin → Maintenance → Sign-in Attempts*.

**Times are wrong.** `TZ` is unset, so the container is on UTC.

**Everyone signed out after an update.** The `instance` volume was not persistent,
so a new signing key was generated. Check your volume mounts.

**"Database not initialised".** The schema is missing — a new install whose
first start was interrupted, or a wiped `instance` volume. Restart the container;
the entrypoint applies migrations on every start. Outside Docker, run
`flask db upgrade`.

**Image previews missing.** Pillow failed to install; check the build log. The app
still works, it just shows file-type chips instead of thumbnails.

**Duplicate work orders from one PM.** More than one worker or more than one
container is running against the same database. Run exactly one.
