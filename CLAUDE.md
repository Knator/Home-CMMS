# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Home CMMS — a self-hosted Flask application for home maintenance management. Tracks assets (appliances, HVAC, plumbing, etc.), work orders (planned and unplanned), job plans (reusable task checklists), and PM schedules that auto-generate work orders on a fixed day interval.

## Tech Stack

- **Backend:** Python 3.11+, Flask 3.x, SQLAlchemy (Flask-SQLAlchemy), Alembic (Flask-Migrate)
- **Database:** SQLite (single file at `instance/home_cmms.db`, no server to run)
- **Auth:** Flask-Login with Werkzeug password hashing; manual CSRF via session token
- **Scheduler:** APScheduler BackgroundScheduler (runs inside Flask process, checks PMs hourly)
- **Frontend:** Jinja2 server-rendered templates, plain CSS, minimal vanilla JS
- **Tests:** pytest

## Environment Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional — every setting has a working default
flask db upgrade              # creates instance/home_cmms.db
python create_admin.py        # create the first admin user
flask run
```

## Common Commands

```bash
flask run                     # start dev server (http://127.0.0.1:5000)
pytest                        # run the test suite
flask db migrate -m "msg"     # generate a new migration after model changes
flask db upgrade              # apply pending migrations
python create_admin.py        # create an admin user interactively
```

## Architecture

### App Factory
`app/__init__.py` uses `create_app(config_class=Config, config_overrides=None)`. Extensions (db, login_manager, migrate) live in `app/extensions.py`. `config_overrides` is how the tests inject a temp database.

The APScheduler is started here, guarded by `WERKZEUG_RUN_MAIN` to prevent double-start with Flask's reloader, and by `SCHEDULER_ENABLED` (set to `0` by `create_admin.py` and the test suite so CLI tools don't spawn scheduler threads).

`migrate.init_app` passes `render_as_batch=True` — SQLite can't `ALTER` most things in place, so Alembic rebuilds tables to apply a migration.

### Blueprints
Each module is a Blueprint under `app/<module>/`:
- `auth` — login/logout/change-password
- `main` — dashboard (route `/`)
- `locations` — flat location list
- `assets` — household assets with category, make/model, dates
- `work_orders` — planned & unplanned WOs; WO numbers are `WO-YYYY-NNNNN`
- `job_plans` — reusable task checklists (JobPlan → JobPlanTask one-to-many)
- `pms` — PM schedules; can manually trigger WO generation or wait for scheduler
- `admin` — user management (admin role only)
- `attachments` — shared download/delete routes for all entity file uploads

Each entity's list view is the `index` endpoint (`url_for('assets.index')`, etc.) — not `list`, which would shadow the builtin.

### Models (`app/models/`)
All models import from `app/extensions.py` (db). Key relationships:
- `Asset` → `Location` (many-to-one)
- `WorkOrder` → `Asset`, `Location`, `JobPlan`, `PM`, `User` (assigned, creator)
- `PM` → `Asset`, `Location`, `JobPlan`
- `JobPlan` → `JobPlanTask` (cascade delete)
- `Attachment` — polymorphic via `entity_type` ('location'|'asset'|'work_order'|'job_plan'|'pm') + `entity_id`

Timestamps use `utcnow()` from `app/utils.py`, not the deprecated `datetime.utcnow`.

### Services (`app/services.py`)
Write paths shared by the routes and the scheduler. **Never insert a `WorkOrder` directly** — go through these, or you skip the WO-number collision retry:
- `create_work_order(**fields)` — allocates the number, commits, retries on `IntegrityError`.
- `generate_work_order_for_pm(pm, ...)` — creates the WO *and* advances the PM in one transaction, so a crash between the two can't produce a duplicate on the next tick.

### PM Scheduler (`app/scheduler.py`)
`run_pm_check(app)` runs hourly. It finds active PMs where `next_due_date <= today` and `last_generated_date` is either NULL or not today (the explicit `IS NULL` matters — SQL treats `NULL != today` as NULL, which would skip brand-new PMs). Each PM commits independently and failures are logged and skipped, so one bad PM can't discard the rest of the pass.

`PM.advance_schedule()` anchors the next due date to the **previous due date**, not to the day it ran, so a late tick or a manual "Generate WO Now" doesn't shift every future occurrence. If a PM is several intervals overdue, whole intervals are skipped — one catch-up WO, not a backlog.

### Auth & Security
- Passwords: `werkzeug.security.generate_password_hash` (pbkdf2:sha256)
- CSRF: `generate_csrf_token()` / `validate_csrf()` in `app/utils.py` (constant-time compare); every POST form includes `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`
- Session cookies: `HTTPONLY=True`, `SAMESITE=Lax`, `SECURE=True` when `FLASK_ENV=production`; `session_protection = 'strong'`
- `SECRET_KEY` is mandatory when `FLASK_ENV=production` — the app refuses to start without it
- The `user_loader` re-checks `is_active` on every request, so deactivating an account ends sessions already signed in
- Login `?next=` goes through `safe_redirect()`, which accepts same-origin relative paths only
- Admin-only routes use `@admin_required` from `app/utils.py`; the last active admin can't be demoted or deactivated
- Posted `status`/`priority`/`wo_type` values are validated against the model's vocabulary; all untrusted ints and dates go through `parse_int`/`parse_date`, which return `None` rather than raising

### File Uploads
Files stored at `UPLOAD_FOLDER/<entity_type>/<entity_id>/<uuid>_<original_name>`. The `attachments.download` route requires login and resolves paths against `UPLOAD_FOLDER`, refusing anything outside it. Max upload size is 50 MB; exceeding it is caught by a 413 handler that flashes a message instead of showing a raw error page.

Attachments have no foreign key (they're polymorphic), so nothing in the database cleans them up. **Every entity delete route must call `purge_entity_attachments(entity_type, id)`** or the rows and files are orphaned.

### SQLite specifics (`app/extensions.py`)
Three pragmas are set on every connection:
- `foreign_keys=ON` — off by default in SQLite, so `ON DELETE CASCADE` is silently ignored without it
- `journal_mode=WAL` — lets the hourly scheduler write while requests read
- `busy_timeout=30000` — wait for a competing writer instead of raising "database is locked"

Run with a **single** worker in production: SQLite allows one writer, and each extra worker would start its own scheduler and generate duplicate work orders.

### Templates & CSS
- Base layout: `templates/base.html` — fixed sidebar + topbar + scrollable content area
- CSS custom properties in `static/css/main.css` (`--sidebar-bg`, `--accent`, etc.)
- Badge classes for WO status and priority are defined on the model (`status_class`, `priority_class`) and applied in templates
- JS in `static/js/main.js` handles dynamic task row add/remove on the job plan form; the `task_count` hidden field it maintains is untrusted input, parsed defensively and capped server-side

### Tests (`tests/`)
`pytest`. Fixtures in `tests/conftest.py` build a throwaway SQLite database and upload directory per test via `create_app(config_overrides=...)`, so tests never touch `instance/home_cmms.db`. `prime_csrf()` seeds the session token so tests can POST without scraping forms.
