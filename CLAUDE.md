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
- `Location` → `Location` (self-referencing parent/child). Names are unique **per
  parent**, not globally, enforced by the `uq_locations_parent_name` functional index on
  `(coalesce(parent_id, -1), lower(name))`. The COALESCE matters: SQL treats NULLs as
  distinct, so a plain `UNIQUE(parent_id, name)` would allow two identically named
  top-level locations. `sibling_name_taken()` mirrors the rule so the form can explain a
  clash instead of surfacing an IntegrityError.
- `Asset` → `Asset` (self-referencing parent/child, for sub-assemblies). Assets carry a
  stable `asset_number` (`AST-00001`); names are deliberately **not** unique, so the number
  is what distinguishes two assets sharing a name. Pickers render
  `name (AST-00001) — location path` for that reason.
- `Asset` → `Location` (many-to-one)
- `WorkOrder` → `Asset`, `Location`, `JobPlan`, `PM`, `User` (assigned, creator)
- `PM` → `Asset`, `Location`, `JobPlan`
- `JobPlan` → `JobPlanTask` (cascade delete)
- `Attachment` — polymorphic via `entity_type` ('location'|'asset'|'work_order'|'job_plan'|'pm') + `entity_id`

Timestamps use `utcnow()` from `app/utils.py`, not the deprecated `datetime.utcnow`.

`app/models/mixins.py` holds the two behaviours Locations and Assets share:
- `LifecycleMixin` — `status` of `active` | `inactive` | `decommissioned`, with
  `status_label`, `status_class` and `is_operational` (named to avoid colliding with
  `User.is_active`). Only Active records appear in work order and PM pickers.
- `HierarchyMixin` — `ancestors`, `descendants`, `depth`, `path_label` and
  `would_create_cycle()`. Both walks are depth-capped and loop-guarded so corrupt data
  can't hang a request.

### Services (`app/services.py`)
Write paths shared by the routes and the scheduler. **Never insert a `WorkOrder` directly** — go through these, or you skip the WO-number collision retry:
- `create_work_order(**fields)` — allocates the number, commits, retries on `IntegrityError`.
- `create_asset(**fields)` — same for `asset_number`. **Never construct `Asset()` directly**;
  `asset_number` is NOT NULL and only this allocates it.
- `generate_work_order_for_pm(pm, ...)` — creates the WO *and* advances the PM in one transaction, so a crash between the two can't produce a duplicate on the next tick.
- `location_delete_blockers(loc)` / `asset_delete_blockers(asset)` — the Maximo rule: a
  record that work has been logged against is never deleted, because that orphans the
  history. Returns human-readable reasons; empty means safe to delete. **Both delete
  routes must consult these**, and the detail templates swap the delete button for a
  "change status instead" panel when non-empty.
- `selectable_locations(include_id)` / `selectable_assets(include_id)` — Active-only
  pickers. `include_id` keeps whatever a record already points at in the list, so
  editing an old work order whose asset was since decommissioned doesn't silently blank
  the field on save.
- `related_attachments(wo)` — documents reachable from a work order's associations: its
  PM, job plan, asset (plus ancestor assets), and location chain (the WO's own location,
  or the asset's when it has none). Returns links to the originals, never copies, so
  updating the asset's manual updates every work order showing it. Labelled with the most
  specific source.
- `hierarchy_ordered(nodes)` — depth-first `[(node, depth)]` for indented tree lists.
  Nodes whose parent was filtered out are promoted to roots so nothing disappears.

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

### Dates
`WorkOrder.completed_date` is a `Date` (not a timestamp) and user-editable. `_resolve_completed_date()`
distinguishes a **missing** form field (leave the existing value alone, so a POST that doesn't
carry the input can't wipe history) from a **present-but-empty** one (clear it). Completing a
work order with no date falls back to today.

**SQLite gotcha for future migrations:** never change a column to `Date`/`DateTime` with
`batch_op.alter_column(type_=...)`. Alembic emits `CAST(col AS DATE)`, and SQLite's DATE has
NUMERIC affinity, so `CAST('2026-08-30' AS DATE)` silently becomes the integer `2026`. Rebuild
the table with `batch_alter_table(copy_from=<explicit table>, recreate='always')` instead —
an uncast copy preserves text that isn't a well-formed number. See migration `713382d2c88b`.

### File Uploads
Files stored at `UPLOAD_FOLDER/<entity_type>/<entity_id>/<uuid>_<original_name>`.

`Attachment.display_name` is an optional friendly label; `att.label` renders it (falling
back to the filename) and `att.download_name` appends the original extension so a label
like "Furnace manual" still saves as a `.pdf`. Rename via `attachments.rename`.

All upload paths go through `store_uploads(entity_type, entity_id, rows, user_id)` in
`app/utils.py`, which validates extensions and returns `(saved, errors)` — a rejected file
is reported without discarding the rest of the batch or the form submission carrying it.
Work order create/edit accept files inline via repeatable `attachment_<i>_file` /
`attachment_<i>_name` rows read by `upload_rows_from_form()` (count is untrusted, so it is
parsed defensively and capped at 10). On create the files are stored *after* the work order
commits, since they are filed under its id.

The shared `templates/_attachments.html` macros (`attachment_list`, `upload_form`,
`related_list`) render attachments on every detail page, so naming and renaming behave
identically everywhere. The `attachments.download` route requires login and resolves paths against `UPLOAD_FOLDER`, refusing anything outside it. Max upload size is 50 MB; exceeding it is caught by a 413 handler that flashes a message instead of showing a raw error page.

Attachments have no foreign key (they're polymorphic), so nothing in the database cleans them up. **Every entity delete route must call `purge_entity_attachments(entity_type, id)`** or the rows and files are orphaned.

### SQLite specifics (`app/extensions.py`)
Three pragmas are set on every connection:
- `foreign_keys=ON` — off by default in SQLite, so `ON DELETE CASCADE` is silently ignored without it
- `journal_mode=WAL` — lets the hourly scheduler write while requests read
- `busy_timeout=30000` — wait for a competing writer instead of raising "database is locked"

Foreign key enforcement is **switchable** via `set_sqlite_foreign_keys()`. `migrations/env.py`
turns it off for the migration connection and disposes the pool so a fresh connection picks
the setting up: Alembic's batch mode rebuilds each table (create temp → copy → drop → rename),
which SQLite cannot do with FKs enforced. Leaving it on makes migrations fail partway and
strand an `_alembic_tmp_<table>` table that blocks every retry. The app's own connections keep
FKs ON.

Run with a **single** worker in production: SQLite allows one writer, and each extra worker would start its own scheduler and generate duplicate work orders.

**Backups:** copy the database with `VACUUM INTO`, not `cp`. In WAL mode a plain file copy
misses everything still in `home_cmms.db-wal` and silently yields a stale snapshot.

### Templates & CSS
- Base layout: `templates/base.html` — fixed sidebar + topbar + scrollable content area
- CSS custom properties in `static/css/main.css` (`--sidebar-bg`, `--accent`, etc.)
- Badge classes for WO status and priority are defined on the model (`status_class`, `priority_class`) and applied in templates
- JS in `static/js/main.js` handles dynamic task rows (job plan form) and attachment rows (work order form); the `task_count` / `attachment_count` hidden fields it maintains are untrusted input, parsed defensively and capped server-side
- `enhanceSearchableSelect()` upgrades any `<select data-searchable>` into a type-to-filter
  combobox (substring match, not prefix). It is **progressive enhancement**: the native
  select stays in the DOM, enabled and named, so it still submits and remains the single
  source of truth — the widget writes `select.value` and dispatches a `change` event, so
  existing listeners keep working. Without JS you get a plain select.
- `initAssetLocationLink()` fetches `/assets/<id>/summary` when the work order form's asset changes and copies the asset's location across, the way Maximo derives location from asset. It only re-derives on asset change, so a location set by hand afterwards survives; if the asset's location is not in the Active-only picker, the option is injected rather than silently failing

### Tests (`tests/`)
`pytest`. Fixtures in `tests/conftest.py` build a throwaway SQLite database and upload directory per test via `create_app(config_overrides=...)`, so tests never touch `instance/home_cmms.db`. `prime_csrf()` seeds the session token so tests can POST without scraping forms.
