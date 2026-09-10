# Running Home CMMS in Docker

One container, two volumes, no external services — no database server, no Redis,
no internet needed at runtime.

---

## Quick start

Runs the published image. No clone, no build.

```bash
mkdir home-cmms && cd home-cmms

curl -O https://raw.githubusercontent.com/Knator/Home-CMMS/master/docker-compose.ghcr.yml
curl -o .env https://raw.githubusercontent.com/Knator/Home-CMMS/master/.env.docker.example
$EDITOR .env                    # at minimum, set TZ

docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

Open `http://<your-host>:8080`. The first start creates the database, generates a
signing key and applies all migrations — a few seconds — then shows a **setup
page** that creates the first administrator.

> **Complete setup straight away.** Until an account exists, anyone who can reach
> the instance can claim the administrator account — the same trade-off Immich,
> Home Assistant, Nextcloud and Gitea make. The page closes permanently once one
> account exists.
>
> To avoid the window entirely, set `ADMIN_USERNAME`/`ADMIN_PASSWORD` so the
> account exists before anything listens, or `SETUP_WINDOW_MINUTES=5` to close
> the page shortly after startup.

### Choosing a version

`IMAGE_TAG` defaults to `latest`, which follows full releases and **never points
at a pre-release**. Pin it for anything you depend on:

```bash
IMAGE_TAG=0.3.0        # a release
IMAGE_TAG=0.3.1-dev    # a pre-release, which latest will not give you
```

Images are built for `linux/amd64` and `linux/arm64`, so a Raspberry Pi works as
well as an x86 box. Each release publishes the full version (`0.3.0`), the minor
series (`0.3`), the major series (`0`), and `latest`.

The image name is **lowercase** — `ghcr.io/knator/home-cmms` — which is not how
the repository is spelled. GHCR rejects capitals.

### Updating

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

Migrations run automatically on start. There is no auto-update; check
deliberately. **Take a backup first** — migrations only run forward, and
restoring a newer backup into an older image is not supported.

---

## Environment variables

All optional; the defaults give a working LAN install. Put them in `.env`.

| Variable | Default | What it does |
|---|---|---|
| `TZ` | `UTC` | Your timezone, e.g. `America/New_York`. **Set this**, or every time reads as UTC. |
| `IMAGE_TAG` | `latest` | Which published version to run. Ignored when building from source. |
| `WEB_PORT` | `8080` | Host port. Two instances cannot share one. |
| `CONTAINER_NAME` | `home-cmms` | Only needed for a second instance; container names are global to Docker, not per compose project. |
| `SECRET_KEY` | *generated* | Signs session cookies. Generated on first run and kept in the instance volume; set it only to manage it yourself. |
| `FLASK_ENV` | *unset* | `production` **only when served over HTTPS**. See below. |
| `TRUST_PROXY_HEADERS` | *off* | `1` **only** behind a reverse proxy you control. See below. |
| `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | *unset* | Unattended first admin. Prefer the setup page, so the password never sits in a file. |
| `SETUP_WINDOW_MINUTES` | `0` | Closes the setup page this many minutes after startup. Restart to reopen. |
| `MAX_UPLOAD_MB` | `100` | Largest single **attachment**. Restoring a backup is exempt, however large. |
| `GUNICORN_TIMEOUT` | `120` | Seconds before a request is killed; generous so a large upload on a slow link survives. |
| `DATABASE_URL` | `sqlite:///instance/home_cmms.db` | Rarely worth changing. |
| `UPLOAD_FOLDER` | `/app/uploads` | Where attachments live inside the container. |

### The two that will lock you out

**`FLASK_ENV=production`** marks session cookies `Secure`, and a `Secure` cookie
is never sent over plain `http://`. Set it without TLS in front and you will
reach the login page, submit correct credentials, and be returned to the login
page with no explanation.

**`TRUST_PROXY_HEADERS=1`** makes the app believe `X-Forwarded-For`. Correct
behind nginx, Caddy or Traefik — dangerous without one, because any client can
then forge its address, escape the sign-in rate limit and poison the audit log.
Leave it *off* and sit behind a proxy, though, and every request appears to come
from the proxy: one person failing to sign in repeatedly locks out everybody.

---

## Building from source

For working on the code. `docker-compose.yml` builds from the checkout, so your
changes are what runs.

```bash
git clone https://github.com/Knator/Home-CMMS.git
cd Home-CMMS
git checkout "$(git describe --tags --abbrev=0)"   # or stay on master to develop
cp .env.docker.example .env
$EDITOR .env
docker compose up -d --build
```

> **`--build` is not optional.** Plain `docker compose up -d` reuses the image it
> built last time, and re-cloning does not help — the stale image is what runs.
> The symptom is code you have already fixed still misbehaving.

Update with `git pull && docker compose up -d --build`.

The two compose files are deliberately not interchangeable, and should not be
merged into one. A file carrying both a `build:` and an `image:` key silently
builds from local source whenever the image is not already present — so you
would believe you were running a published release while running your working
tree.

---

## What lives where

Two volumes. **Both must persist.**

**`/app/instance`** — small and critical: `home_cmms.db` (the entire database),
its `-wal`/`-shm` sidecars, the generated `secret_key` (mode 0600; losing it
signs everybody out but loses no data), and `backups/`.

**`/app/uploads`** — attachments and asset photos, filed as
`<entity-type>/<id>/<uuid>_<filename>`, plus a `.thumbnails/` cache that
regenerates on demand and never needs backing up. This dwarfs the database: a
handful of phone photos is easily 50 MB against a few hundred KB.

---

## Backups

**Never copy `home_cmms.db` with `cp`, `tar` or a volume snapshot while the app
is running.** SQLite runs in WAL mode, so recent writes live in
`home_cmms.db-wal`. A plain copy misses them and produces a database that *opens
fine* and is quietly out of date — the worst kind of bad backup.

**The easy way:** *Admin → Maintenance → Backups → Create backup* writes one
`.tar.gz` holding a consistent database snapshot and every uploaded file. Set
**Keep** to prune old ones. Download it somewhere else — a backup inside the same
volume protects you from nothing.

**Scriptable, while running:**

```bash
docker compose exec -T cmms python -c \
  "import sqlite3; sqlite3.connect('instance/home_cmms.db').execute('VACUUM INTO ?', ('instance/backups/db-snapshot.db',))"
docker compose cp cmms:/app/instance ./backup-instance
docker compose cp cmms:/app/uploads  ./backup-uploads
```

**Stopped**, any copy method is safe:

```bash
docker compose stop
docker run --rm -v home-cmms_cmms-instance:/i -v home-cmms_cmms-uploads:/u \
  -v "$PWD":/out alpine tar czf /out/home-cmms-backup.tar.gz /i /u
docker compose start
```

### Restoring

*Admin → Maintenance → Restore.* Pick a backup from `instance/backups` or upload
one, tick the confirmation, press Restore. The app validates the archive before
touching anything, saves the current state as `pre-restore-<timestamp>.tar.gz`
(never pruned by **Keep**), replaces the database and uploads, applies pending
migrations so an older backup still opens, then rotates `SECRET_KEY` and signs
everyone out — a restored database can map an existing session cookie to a
different account.

The restore upload has **no size limit**; `MAX_UPLOAD_MB` bounds attachments, not
backups. A large archive is still quicker to place directly:

```bash
docker compose cp home-cmms-backup-YYYYMMDD-HHMMSS.tar.gz cmms:/app/instance/backups/
```

**Moving to a new host:** start a fresh instance and use "Restore from a backup
instead" on the setup page, then sign in with an account from the backup. There
is no confirmation step and no `pre-restore` copy, because an instance with no
users has nothing to lose. A backup containing no accounts is refused, rather
than leaving an instance nobody can sign in to.

**By hand**, with the stack down:

```bash
docker compose down
mkdir restore && tar -xzf home-cmms-backup-YYYYMMDD-HHMMSS.tar.gz -C restore

docker run --rm -v home-cmms_cmms-instance:/i -v "$PWD/restore":/r alpine sh -c \
  "cp /r/home_cmms.db /i/home_cmms.db && rm -f /i/home_cmms.db-wal /i/home_cmms.db-shm"
docker run --rm -v home-cmms_cmms-uploads:/u -v "$PWD/restore":/r alpine sh -c \
  "cp -r /r/uploads/. /u/"

docker compose up -d
```

Backups hold the database and `uploads/` — not `secret_key`, so everyone signs
out once either way and signs back in with credentials from the restored
database.

**Test your restore before you need it.** Restore into a scratch stack and log
in. An untested backup is a hope, not a backup.

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

`docker compose down -v` removes the volumes — your database and every
attachment. There is no undo.

> Add `-f docker-compose.ghcr.yml` to these when running the published image.

---

## Running a second instance

Give the second stack its own name and port in its `.env`:

```bash
CONTAINER_NAME=home-cmms-dev
WEB_PORT=8081
```

Two things collide otherwise, and only one is obvious. **Container names are
global to the Docker daemon**, not scoped per compose project, so the second
stack fails with `Conflict. The container name "/home-cmms" is already in use`.
**Ports** collide next, once the name is fixed.

Volumes need no attention — they are named per project, so the second instance
gets its own empty database. The two share nothing.

---

## Behind a reverse proxy

Terminate TLS at the proxy and set both `FLASK_ENV=production` and
`TRUST_PROXY_HEADERS=1`.

```
cmms.example.com {
    reverse_proxy localhost:8080
}
```

Caddy sets `X-Forwarded-For` and `X-Forwarded-Proto` itself, which is what those
two settings depend on.

---

## Security notes

The container runs as an unprivileged user (uid 10001) with `no-new-privileges`,
and writes only to its two volumes. Built in: sign-in rate limiting with lockout,
hashed passwords, hashed API tokens, CSRF on every form, an upload allowlist, and
a signing key generated per install rather than shipped.

Worth knowing before exposing it to the internet:

- **Everyone signed in can see and edit everything.** The `admin` role only gates
  user management, Settings and Maintenance. Hand out accounts only to people you
  trust with all of the data.
- **`/api/v1/docs` is public** — the shape of the API, never any data. Add
  `@login_required` to the two view functions in `app/api/routes.py` to change
  that.
- **`FLASK_DEBUG` is refused** on any non-loopback host: the debugger executes
  arbitrary code, so the app will not start in that configuration.
- Passwords require 8 characters and nothing else. Internet-facing, choose better
  ones than that implies.
- Keep it on your LAN unless you have a reason not to.

---

## Troubleshooting

**Can't sign in, no error shown.** Almost always `FLASK_ENV=production` without
HTTPS. Unset it and restart.

**Everyone is locked out at once.** Behind a proxy without
`TRUST_PROXY_HEADERS=1`, so every request shares the proxy's address. Set it, or
clear the lockouts under *Admin → Maintenance → Sign-in Attempts*.

**`denied` when pulling the image.** The GHCR package is private. On GitHub:
your profile → Packages → `home-cmms` → Package settings → Change visibility →
Public. Needed once.

**Times are wrong.** `TZ` is unset, so the container is on UTC. Confirm under
*Admin → Maintenance → System*.

**Everyone signed out after an update.** The `instance` volume was not
persistent, so a new signing key was generated. Check your volume mounts.

**"Database not initialised".** The schema is missing — an interrupted first
start, or a wiped `instance` volume. Restart the container; the entrypoint
applies migrations every start.

**Image previews missing.** Pillow failed to install; check the build log. The
app still works, showing file-type chips instead of thumbnails.

**Duplicate work orders from one PM.** More than one worker or container is
running against the same database. Run exactly one: SQLite takes a single
writer, and the PM scheduler runs inside the app, so a second copy generates the
same work orders again. One worker is ample for a household — the compose files
are already set up that way, and this only bites if you change it.

**Fixed code still misbehaving.** You built without `--build`, so the old image
is still running. Only applies when building from source.
