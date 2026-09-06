# Home CMMS

A self-hosted maintenance management system for your home.

Track the things you own, the work you do on them, and the maintenance that comes around every few months — furnace filters, water heater flushes, gutter cleaning — without keeping any of it in your head.

One container, two volumes, no external services. No database server, no Redis, no internet connection required at runtime.

![The Home CMMS dashboard](docs/images/dashboard.jpg)

---

## Why

Commercial CMMS software is built for factories and priced for them. Spreadsheets and calendar reminders are the usual home alternative, and both fall down the moment you ask a question like *"which filter size does the upstairs unit take, and when did I last change it?"*

Home CMMS borrows the parts of industrial maintenance software that actually matter at home — assets, work orders, job plans, PM schedules — and drops the rest.

## What's in it

**Assets** — appliances, HVAC, plumbing, whatever you want to track. Each gets a location, make/model, dates, photos, and a running
list of the parts you've used on it. Assets nest, so a compressor can live inside he HVAC unit it belongs to.

**Locations** — rooms, floors, outbuildings, also nested. Both assets and locations carry a status (Active / Inactive / Decommissioned).

**Work orders** — planned and unplanned, with
priorities, due dates, assignees, and a checklist of tasks. Each carries its own materials and tools list.

**Job plans** — like reusable checklists or job instructions. Tasks can have time estimates, plus the materials and tools the job needs. Attach a job plan to a work order or PMs.

**PM schedules** — **P**reventative **M**aintneance schedules generate work orders automatically based on rules that you set, either keeping a fixed calendar rhythm or floating from when you last actually finished the job. Lead times and grace windows give you flexibility to when work should be completed before showing overdue, when still being completed in an acceptable window.

**Attachments** — Easily attach manuals, receipts, photos, etc to locations, asseets, job plans, and work orders so that documents can easily be referenced, and pictures of parts or work performed can be easily reviewed later. 

**Also:** a REST API with per-integration bearer tokens and its own OpenAPI docs at `/api/v1/docs`, an admin area for users and housekeeping, backups and restore from the browser.

---

## Quick start

```bash
git clone https://github.com/Knator/Home-CMMS.git
cd Home-CMMS
cp .env.docker.example .env
$EDITOR .env                     # at minimum, set TZ
docker compose up -d --build
```

Open `http://<your-host>:8080`. On a fresh instance you land on a setup page that creates the first administrator account.

> **Complete setup straight away.** Until an account exists, anyone who can reach the instance can claim the administrator account. The page closes permanently once one account exists. Don't put a fresh instance on an untrusted network before finishing setup.

> **Pass `--build` whenever the source changes.** This stack builds from the repository rather than pulling a published image, so a plain `docker compose up -d` alone reuses the image it built last time — and re-cloning doesn't help, because the stale image is what runs.

The first start creates the database, generates a signing key, and applies all migrations. [`DOCKER.md`](DOCKER.md) covers every environment variable, reverse proxy setup, backups, restores and the security notes.

### Without Docker

Every setting has a working default, so there's nothing to configure to try it:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask db upgrade
flask run
```

Then open `http://127.0.0.1:5000` and complete the setup page. Python 3.11+.

---

## Backups

The Maintenance page writes one archive holding the database and every uploaded file, and restores from the same place — or from the setup screen, which is how you move an instance to a new machine. Restores validate the archive before touching anything and keep a copy of the previous state.

Download them somewhere else. A backup sitting beside the original is not a backup.

## Notes on running it

SQLite takes a single writer and the PM scheduler runs inside the application, so this runs as **one worker** by design. That is sufficient for a household and is why there's no database server to look after.

It expects to live on your LAN. There's no multi-tenancy and no ambition toward it at this time.

## License

[GPL-3.0](LICENSE)
