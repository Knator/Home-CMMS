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

## Deployment
`Dockerfile`, `docker-compose.yml` and `docker/entrypoint.sh` package the app; `DOCKER.md`
is the user-facing guide. The entrypoint runs `flask db upgrade`, optionally seeds an admin
from `ADMIN_*`, then execs gunicorn with **one worker** — SQLite takes a single writer and
the PM scheduler lives in-process, so a second worker means duplicate work orders. The image
runs as uid 10001 and persists `/app/instance` and `/app/uploads`.

`TRUST_PROXY_HEADERS` (off by default) enables `ProxyFix`. It must stay off without a proxy
in front, or a client can forge `X-Forwarded-For` to dodge the sign-in rate limit; it must be
**on** behind one, or every request shares the proxy's address and one failing user locks
everybody out.

`create_admin.py` takes `--username/--email/--password` or `ADMIN_*`, plus `--if-missing` so
the entrypoint can run it on every start.

## Environment Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional — every setting has a working default
                              # (Docker uses .env.docker.example instead)
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
- `Location` → `Location` (self-referencing parent/child). Locations carry a stable
  `location_number` (`LOC-00001`), allocated by `create_location()`; **never construct
  `Location()` directly**. Names are unique **per
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
- `JobPlan` → `JobPlanTask` (cascade delete) and `JobPlanItem` (cascade delete).
  `JobPlanItem` carries required **materials and tools** in one table with a `kind`
  discriminator — they are structurally identical (ordered line, description, optional
  quantity), so one table beats two near-duplicate models. `JobPlan.materials` /
  `.tools` filter by kind; `JobPlan.total_minutes` sums the task estimates.
- `Attachment` — polymorphic via `entity_type` ('location'|'asset'|'work_order'|'job_plan'|'pm') + `entity_id`

### Time
Timestamps are **stored in UTC** (`utcnow()`) and converted to the **host's timezone only for
display**, via `format_datetime()` — registered as a Jinja global, so templates never call
`.strftime()` on a DateTime directly.

Storing host-local time instead would be simpler but lossy: a daylight-saving fallback makes
the same wall-clock hour occur twice, so ordering and ambiguity break, and every stored value
silently changes meaning if the machine's timezone changes. UTC storage also meant no data
migration — existing rows were already correct, only the display was wrong.

`to_local()` uses `astimezone()` with no argument, which reads the operating system's
timezone. No timezone database of its own, and no network — set `TZ` in a container and the
app follows.

**Date columns are not timestamps.** `due_date`, `completed_date`, `next_due_date`,
`install_date` and friends are local calendar dates set from `date.today()`; converting them
would shift a due date by a day. Only DateTime columns go through `format_datetime()`.

`app/models/mixins.py` holds the two behaviours Locations and Assets share:
- `LifecycleMixin` — `status` of `active` | `inactive` | `decommissioned`, with
  `status_label`, `status_class` and `is_operational` (named to avoid colliding with
  `User.is_active`). Only Active records appear in work order and PM pickers.
- `HierarchyMixin` — `ancestors`, `descendants`, `depth`, `path_label` and
  `would_create_cycle()`. Both walks are depth-capped and loop-guarded so corrupt data
  can't hang a request.

### Materials and tools
`JobPlanItem` and `WorkOrderItem` share `ItemFieldsMixin` (kind, sequence, description,
quantity, part_number) but are **separate tables on purpose**: a work order's list is a
*snapshot* taken when it was raised, so editing a job plan later must not rewrite the record
of work already done against the old one.

- `copy_job_plan_items(wo)` seeds a work order from its job plan, but **only when the work
  order has no lines of its own** — re-copying would discard edits made for that specific job.
  A work order can carry materials and tools with no job plan at all.
- `record_materials_on_asset(wo)` rolls a completed work order's **materials** (not tools)
  onto `AssetMaterial`, so months later the asset itself answers "what part did I use?".
  Called only on the transition *into* completed, so re-saving cannot double-count.
- `_match_existing_material()` decides what counts as the same part: a part number matches its
  twin exactly, and may adopt a numberless row with the same description (so a number learned
  later fills in rather than forking). A row with a *different* number is never merged. Items
  with no number match on description alone.
- `AssetMaterial.last_work_order_id` is `ON DELETE SET NULL` — deleting the work order must
  leave the part on record, which is the entire point of the feature.

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

A PM schedules one of two ways, chosen by `schedule_from_completion` ("Schedule Based on
Last Completed"):
- **Fixed** (default): the calendar rhythm is kept — see `advance_schedule()` below.
- **Floating**: `reschedule_from_completion()` re-anchors `next_due_date` to the *latest*
  completion across all the PM's work orders, plus the interval. Using the latest rather
  than whichever work order was just saved makes it idempotent and stops out-of-order
  completions dragging the schedule backwards. It is driven by completion events, not by
  the scheduler: `sync_pm_schedule()` is called from the work order create and edit paths,
  and by the PM edit route so switching the flag on takes effect immediately.

`wo_priority` is the priority stamped on the work orders a PM generates (default `medium`).

`generate_lead_days` opens the generation window early: a PM becomes eligible on
`next_due_date - lead`, while the work order still carries the real due date. The form
enforces `lead < interval_days`, because a lead at or above the interval would put the
next occurrence inside its own window the moment it was scheduled and generate again the
following day. `run_pm_check` prefilters in SQL with a `MAX_LEAD_DAYS` window and applies
each PM's exact lead in Python, since the lead is a column.

`overdue_grace_days` (on both PM and WorkOrder) delays the overdue flag: `overdue_from` is
`due_date + grace + 1`. A work order generated from a PM inherits the PM's grace. The
dashboard's overdue count is settled in Python from the same `is_overdue` the pages use —
`due_date < today` remains a cheap SQL prefilter, since grace can only ever make fewer
records overdue.

Generation still advances the due date in both modes — otherwise a floating PM would
regenerate every day until someone completed the work.

`PM.advance_schedule()` anchors the next due date to the **previous due date**, not to the day it ran, so a late tick or a manual "Generate WO Now" doesn't shift every future occurrence. If a PM is several intervals overdue, whole intervals are skipped — one catch-up WO, not a backlog.

### First-run setup (`app/setup/`)
While `User.query.count() == 0`, `/setup` offers to create the first administrator and a
`before_request` gate redirects everything else there (the API answers 503 JSON; the public
API docs stay reachable). One account closes it permanently — the POST re-checks inside the
request so two simultaneous visitors cannot both become admin.

This is the pattern Immich, Home Assistant, Nextcloud and Gitea use, and it carries the same
residual risk: until the account exists, whoever reaches the page first gets it. Mitigations,
in increasing order of strictness: a loud warning logged on every start while it is open;
`SETUP_WINDOW_MINUTES` to bound the window Portainer-style; or `ADMIN_*` env vars, which
create the account before anything listens and close the window entirely.

### Auth & Security
- Passwords: `werkzeug.security.generate_password_hash` (pbkdf2:sha256)
- CSRF: `generate_csrf_token()` / `validate_csrf()` in `app/utils.py` (constant-time compare); every POST form includes `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`
- Session cookies: `HTTPONLY=True`, `SAMESITE=Lax`, `SECURE=True` when `FLASK_ENV=production`; `session_protection = 'strong'`
- **"Remember me" is a second cookie Flask-Login manages, and it does not inherit any
  `SESSION_COOKIE_*` setting.** Left at its defaults it was a *year*-long login token with no
  `Secure` and no `SameSite` — the more valuable of the two cookies, protected less well than
  the session. `REMEMBER_COOKIE_*` now mirrors the session's settings at 30 days. `SECURE` is
  conditional on `FLASK_ENV=production` for both: a Secure cookie is never sent over plain
  http, so forcing it on would silently break login-persistence for a LAN install with no TLS.
- `PERMANENT_SESSION_LIFETIME` only applies to a *permanent* session, which `login_user()`
  does not set — so the configured 8 hours was inert and sessions simply lasted until the
  browser closed. The login route now sets `session.permanent = True`; Flask refreshes it per
  request, making it an 8-hour **idle** timeout rather than an absolute one.
- **`SECRET_KEY` has no shipped default.** A constant in the source would be identical on
  every self-hosted install, and anyone holding it can forge a session cookie for any
  account. Resolution order: `SECRET_KEY` env var, then `instance/secret_key`, then generate
  one and persist it at mode 0600. Keep `instance/` on a volume in a container or sessions
  reset on every restart.
- **Brute-force protection** (`app/security.py`): failures are recorded in `auth_attempts`,
  not process memory, so a lockout is not cleared by restarting and the log doubles as the
  audit trail. Two independent limits — 5 failures per identifier and 20 per source address
  in 15 minutes — because the second is what catches spraying across many accounts, where no
  single account trips the first. A success clears that identifier's failures. Admins can
  release everything from the maintenance page, which is the escape hatch for locking
  yourself out. Behind a reverse proxy every request appears to come from the proxy unless
  the app is configured to trust forwarded headers, which would neuter the per-address limit.
- **`run.py` refuses to start with `FLASK_DEBUG` on and a non-loopback `HOST`.** The Werkzeug
  debugger executes arbitrary Python from the browser; on a network interface that is remote
  code execution for anyone who can reach the port.
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

Every file input accepts a **selection**, not just one file. `named_uploads(files, name)`
pairs them up and drops the friendly name when more than one file was chosen, since one
label cannot describe a whole selection — those keep their filenames. Repeatable form rows
take a selection per row too.

All upload paths go through `store_uploads(entity_type, entity_id, rows, user_id)` in
`app/utils.py`, which validates extensions and returns `(saved, errors)` — a rejected file
is reported without discarding the rest of the batch or the form submission carrying it.
Work order create/edit accept files inline via repeatable `attachment_<i>_file` /
`attachment_<i>_name` rows read by `upload_rows_from_form()` (count is untrusted, so it is
parsed defensively and capped at 10). On create the files are stored *after* the work order
commits, since they are filed under its id.

Image attachments show a **thumbnail** in every list, generated by
`build_thumbnail()` and cached to `UPLOAD_FOLDER/.thumbnails/<id>.jpg` — resizing a phone
photo on every page view would be wasteful, and the source never changes because a
replacement is a new attachment id. Generation applies `ImageOps.exif_transpose` (phone
photos carry rotation in EXIF and come out sideways without it) and flattens transparency,
since RGBA cannot be saved as JPEG. Two details that were found the hard way against real photos:
- `img.draft()` is called before any pixel access, so a JPEG is decoded at reduced scale —
  a 200 MP panorama thumbnails in 29 MB of RAM instead of ~600 MB.
- `THUMBNAIL_MAX_PIXELS` raises Pillow's ~179 MP decompression-bomb guard, which was
  rejecting legitimate phone panorama/super-resolution shots outright. Raised, not removed.

Pillow is optional, and the degradation must stay graceful: `thumbnails_available` is
computed once at startup and exposed as a Jinja global, so **without Pillow the templates
skip previews entirely** rather than requesting images they cannot shrink. The route also
refuses to stand in with an original larger than `THUMBNAIL_FALLBACK_MAX_BYTES` — serving a
13 MB photo into a 48px box stalled desktop scrolling for seconds, which is worse than no
preview. A thumbnail that still fails degrades to the extension chip via `onerror`. The cache lives outside the per-entity upload directory, so
`purge_entity_attachments()` and the delete routes clear it explicitly.

Clicking a thumbnail opens the full image in a lightbox via `attachments.inline`
(`as_attachment=False`), so it is viewed rather than downloaded. The anchor's `href` points
at the same URL, so without JavaScript the click just opens the image in a new tab.

An asset may have one optional photo: `Asset.image_attachment_id` points at an ordinary
Attachment row, so upload, storage and deletion reuse the same plumbing. The FK is
`ON DELETE SET NULL`, so deleting the underlying file — directly or by purging the asset —
cannot leave a dangling reference. Photos are served by `attachments.inline`, which sends
the file with `as_attachment=False` and `X-Content-Type-Options: nosniff`, and refuses
anything outside `IMAGE_EXTENSIONS` (raster only; SVG is excluded because it can carry
script). The photo renders on the asset detail page only.

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
- **Responsive**: three breakpoints in `main.css` (860px, 700px, 480px). Below 860px the
  sidebar becomes an off-canvas drawer behind `#nav-toggle`, driven by a `body.nav-open`
  class so CSS owns the animation and `initMobileNav()` only manages state; two-column
  grids collapse; inputs go to 16px because iOS zooms the page for anything smaller.
  Below 700px, table columns marked `col-hide-sm` drop out — nothing is lost, the detail
  page still shows every field, and the primary identifying columns are never hidden.
  Heights use `100dvh` with a `100vh` fallback, since `100vh` includes mobile browser
  chrome and makes the bottom of the page unreachable.
- Layout must not be inlined in templates (`style="grid-template-columns:..."`), or it
  cannot respond to the breakpoints — use `.split-grid` / `.form-shell` instead
- CSS custom properties in `static/css/main.css` (`--sidebar-bg`, `--accent`, etc.)
- Badge classes for WO status and priority are defined on the model (`status_class`, `priority_class`) and applied in templates
- JS in `static/js/main.js` drives **repeatable form rows** — tasks, materials, tools and
  attachments — from one `ROW_TYPES` config rather than four near-copies. Inputs are named
  `<kind>_<index>_<field>` with a hidden `<kind>_count`; the server re-reads them in DOM
  order, so **reordering rows is what reorders the saved sequence**. Rows are drag-reorderable
  and also carry ▲▼ buttons, since drag alone is unreachable from a keyboard. Every
  `<kind>_count` is untrusted input, parsed defensively and capped server-side
- `enhanceSearchableSelect()` upgrades any `<select data-searchable>` into a type-to-filter
  combobox (substring match, not prefix). It is **progressive enhancement**: the native
  select stays in the DOM, enabled and named, so it still submits and remains the single
  source of truth — the widget writes `select.value` and dispatches a `change` event, so
  existing listeners keep working. Without JS you get a plain select.
- **Creating a record from the picker that needs it** (`_pickers.html`, `initCreateModal()`).
  Every asset / location / job-plan `<select>` on a form carries a `+`. It is a real link to
  the real create page (`target="_blank"`, `rel="noopener"`), so with JS off it opens in a
  tab and nothing is lost; JS upgrades it into a dialog framing that same page with
  `?embedded=1`. The form underneath usually holds a half-finished work order, so navigating
  away is exactly what must not happen.
  The dialog uses an **iframe of the real page**, not a second copy of the form: same fields,
  same validation, same CSRF, nothing to keep in step. `?embedded=1` is read by a context
  processor that swaps `layout` from `base.html` to `embedded.html`, which is why every form
  template says `{% extends layout %}` and no `render_template` call had to change.
  On success the create route returns `embedded_created()` instead of redirecting — a page
  that `postMessage`s the new record to the opener, which inserts the option, selects it and
  dispatches `change` (so the searchable combobox updates *and* a new asset's location is
  inherited). `targetOrigin` is the instance's own origin, never `'*'`, and the listener
  checks both origin and a `source: 'home-cmms'` marker. Validation errors re-render inside
  the dialog because the query string rides along with the POST.
- `initFieldTooltips()` mirrors each text field's value into its `title`, so hovering shows
  content too long for the box. It skips password fields and leaves an author-supplied
  `title` alone, and is re-run for dynamically added rows.
- `initAssetLocationLink()` copies a location across from another record by fetching
  `/assets/<id>/summary`, the way Maximo derives location from asset. It drives **three**
  forms from one implementation: work orders and PMs (from the asset) and the asset form
  itself (from the **parent asset**). A form opts in with `data-summary-url` on any select
  plus `id="location_id"` and a `#location-hint` span; `data-summary-label` names the source
  in the hint. It **must dispatch a `change` event** after setting `location.value`: the
  searchable combobox only re-reads the select on `change`, so a bare assignment updates the
  submitted value but not what the user sees. If the location is not in the Active-only
  picker, the option is injected rather than silently failing.
  `data-only-when-empty` splits the two behaviours. Work orders and PMs re-derive on every
  asset change, because the asset *determines* the location. The asset form sets it only
  when blank: a child asset may legitimately sit somewhere other than its parent, so
  relocating it on a parent change would destroy a real choice. The code tracks the value it
  wrote itself and will replace that — otherwise the first auto-fill would freeze the field
  and picking a different parent would appear to do nothing. `test_location_inheritance.py`
  pins the attribute contract from both ends, since a rename silently disables the feature

### REST API (`app/api/`, `/api/v1`)
Records are addressed by their **numbers** (`AST-00001`, `LOC-00003`), never names: asset
names are deliberately not unique and location names are only unique per parent, so a name
is not something a client can reliably address.

- **Auth**: named bearer tokens in `api_tokens` (`Authorization: Bearer <token>` or
  `X-API-Key`). A user may hold several — one per integration — so one can be revoked without
  disturbing the rest, and the name records where it is used. Only a SHA-256 digest is stored;
  tokens are high-entropy random strings, so a fast digest is enough and a leak yields nothing
  usable. `last_used_at` records activity. Issued and revoked per token from the admin user
  page, and the plaintext is rendered alone in a readonly input with a copy button — never
  embedded in prose, so it can be copied without dragging surrounding text along.
- **Clipboard**: `initCopyButtons()` falls back to `document.execCommand('copy')`, because
  `navigator.clipboard` only exists in a secure context and this app is normally reached over
  plain http on a LAN address — the fallback is the usual path, not a legacy one.
- **Errors**: one envelope everywhere — `{"error": "...", "errors": {"field": "why"}}`. All
  field problems are reported in a single response rather than one at a time. 400 for
  validation (including a number that resolves to nothing, or to a non-Active record), 401
  for auth, 404/405 as JSON via app-level handlers that check the path — Flask raises
  routing errors before a blueprint is known, so a blueprint handler would not fire.
- **Consistency with the UI**: retired assets and locations are refused, an asset's location
  is inherited when none is given, and creation goes through `create_work_order()` so number
  allocation and its retry are shared.

**Documentation** lives in one place, `app/api/docs.py`, and is rendered two ways:
`/api/v1/openapi.json` (an OpenAPI 3.1 document for Swagger UI, Postman, Insomnia) and
`/api/v1/docs` (a human page, server-rendered with the app's own CSS). **Both are public** —
they describe the shape of the API, not its contents, and every endpoint they document is
still behind a token. That lets tooling which cannot hold a session (Swagger UI, an OpenAPI
linter) fetch the spec directly.

Because they are public, **examples in `docs.py` must stay generic** — never copied from a
real deployment, or the reference broadcasts someone's room and equipment names.
`test_examples_are_generic_not_copied_from_the_instance` enforces that. `base.html` also
renders for a visitor with no session: app navigation and the user menu appear only once
signed in.

The page is deliberately **not** a CDN-hosted Swagger UI — this app often runs on an offline
LAN box, where that renders blank. **When adding an endpoint, add an entry to `ENDPOINTS`**:
`test_api_docs.py` fails if a route is undocumented *or* documented but missing, which is
what stops the reference drifting from the code.

### Maintenance (`app/maintenance.py`, `/admin/maintenance`)
Admin-only housekeeping, modelled on what self-hosted apps generally need (Home Assistant's
backups and system health, Immich's orphaned-file repair, LubeLogger's single-archive export):

- **Backups** — one `.tar.gz` of the database plus `uploads/`. The database is captured with
  `VACUUM INTO`, never a file copy, because in WAL mode a copy silently misses everything
  still in the `-wal` sidecar. Thumbnails are excluded; they rebuild. Optional retention
  prunes to the newest N.
- **Restore** (`restore_backup()`) replaces the database and uploads from an archive, from
  the maintenance page or the first-run setup screen. Order matters and is the whole design:
  `inspect_backup()` validates first so a wrong file costs nothing; a `pre-restore-<stamp>`
  safety copy is taken; the database is swapped with the engine disposed and the `-wal`/`-shm`
  sidecars deleted (they belong to the replaced file); then `flask db upgrade` runs so an
  older backup opens. `_safe_members()` rejects absolute paths, `..`, links and anything
  outside `home_cmms.db`/`uploads/` — a tar member is an arbitrary write primitive otherwise.
  A **failed safety copy does not block the restore**: it usually fails because the current
  database is unreadable, which is exactly when someone is restoring.
  `rotate_secret_key()` then invalidates every session, because the restored database can
  map a session cookie's user id to a different account; it returns False when `SECRET_KEY`
  comes from the environment, which the app cannot change.
  Safety copies are ordinary backups (`is_backup_name()` accepts both prefixes, so they
  list, download and delete) but `prune_backups()` skips them — pruning away the undo copy
  is exactly the moment someone needs it.
  The setup-screen path takes no safety copy and no confirmation (an instance with no users
  has nothing to lose) and refuses a backup containing no accounts, which would otherwise
  leave an instance nobody can sign into.
  Both restore routes call `allow_large_upload()` **before touching `request.form`**, which
  lifts `MAX_CONTENT_LENGTH` for that request: a backup is the whole instance in one file,
  and the cap exists to bound a single attachment. Exceeding it does not yield a tidy 413
  either — the body is cut off mid-upload and the browser reports a connection reset.
- `_replace_directory_contents()` swaps what is *inside* `UPLOAD_FOLDER`, never the
  directory itself. It is a **volume mount point** in Docker, and a mount point cannot be
  renamed: `os.rename(uploads, uploads + '.replaced')` fails with EBUSY. Uploads are also
  replaced *before* the database now, so the least reliable step cannot fail after the
  point of no return and leave records pointing at files that were never restored.
- **Storage integrity** — attachments are polymorphic with no foreign key, so the table and
  the filesystem can drift. `scan_storage()` is read-only and reports records whose file is
  missing, records whose owning entity is gone, files nothing references, and stale
  thumbnails; `clean_storage()` is a separate explicit step.
- **Database** — `PRAGMA integrity_check` + `foreign_key_check`, `VACUUM` (on an AUTOCOMMIT
  connection, since it cannot run in a transaction), and a WAL checkpoint.
- **Scheduler visibility** — shows whether the PM job is running and its next fire time, with
  a "run now" button. The scheduler is registered at `app.extensions['pm_scheduler']`.

Backup filenames coming from a URL go through `is_backup_name()` before any path join.

The actions run **in place**. Each is an ordinary POST form that works with JS disabled;
`initAsyncActions()` intercepts submits, sends them with `X-Requested-With: fetch`, and swaps
the `#maintenance` region with the server's re-rendered markup — so the server stays the only
thing that renders the page and no view logic is duplicated in JS. `_maintenance_result()`
returns the re-render for a fetch and a redirect otherwise. If the response is not the
expected page (an expired session returning the login screen, say) the browser reloads rather
than swapping in something wrong. Downloads deliberately carry no `data-async`, since they
must stay real navigations.

### Tests (`tests/`)
`pytest`. `tests/test_js_logic.py` runs the real `main.js` under dukpy against the tiny DOM
shim in `tests/js_shim.js`, so the browser logic is covered rather than eyeballed — three
defects reached the repo before it existed. It skips cleanly if dukpy is absent. Fixtures in `tests/conftest.py` build a throwaway SQLite database and upload directory per test via `create_app(config_overrides=...)`, so tests never touch `instance/home_cmms.db`. `prime_csrf()` seeds the session token so tests can POST without scraping forms.
