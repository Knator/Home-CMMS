# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Home CMMS — a self-hosted Flask application for home maintenance management. Tracks assets (appliances, HVAC, plumbing, etc.), work orders (planned and unplanned), job plans (reusable task checklists), and PM schedules that auto-generate work orders on a fixed day interval.

## Tech Stack

- **Backend:** Python 3.14, Flask 3.x, SQLAlchemy (Flask-SQLAlchemy), Alembic (Flask-Migrate)
- **Database:** PostgreSQL via psycopg2-binary
- **Auth:** Flask-Login with Werkzeug password hashing; manual CSRF via session token
- **Scheduler:** APScheduler BackgroundScheduler (runs inside Flask process, checks PMs hourly)
- **Frontend:** Jinja2 server-rendered templates, plain CSS, minimal vanilla JS

## Environment Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # edit DATABASE_URL and SECRET_KEY
flask db upgrade              # run migrations
python create_admin.py        # create the first admin user
flask run
```

## Common Commands

```bash
flask run                     # start dev server (http://127.0.0.1:5000)
flask db migrate -m "msg"     # generate a new migration after model changes
flask db upgrade              # apply pending migrations
python create_admin.py        # create an admin user interactively
```

## Architecture

### App Factory
`app/__init__.py` uses `create_app()`. Extensions (db, login_manager, migrate) live in `app/extensions.py`. The APScheduler is started here, guarded by `WERKZEUG_RUN_MAIN` to prevent double-start with Flask's reloader.

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

### Models (`app/models/`)
All models import from `app/extensions.py` (db). Key relationships:
- `Asset` → `Location` (many-to-one)
- `WorkOrder` → `Asset`, `Location`, `JobPlan`, `PM`, `User` (assigned, creator)
- `PM` → `Asset`, `Location`, `JobPlan`
- `JobPlan` → `JobPlanTask` (cascade delete)
- `Attachment` — polymorphic via `entity_type` ('location'|'asset'|'work_order'|'job_plan'|'pm') + `entity_id`

### PM Scheduler (`app/scheduler.py`)
Runs `run_pm_check(app)` every hour. Finds active PMs where `next_due_date <= today` and `last_generated_date != today`. For each, creates a planned WorkOrder and calls `pm.advance_schedule()` which sets `next_due_date = today + interval_days`.

### Auth & Security
- Passwords: `werkzeug.security.generate_password_hash` (pbkdf2:sha256)
- CSRF: `generate_csrf_token()` / `validate_csrf()` in `app/utils.py`; every POST form includes `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`
- Session cookies: `HTTPONLY=True`, `SAMESITE=Lax`, `SECURE=True` when `FLASK_ENV=production`
- Admin-only routes use `@admin_required` decorator from `app/utils.py`

### File Uploads
Files stored at `UPLOAD_FOLDER/<entity_type>/<entity_id>/<uuid>_<original_name>`. The `attachments.download` route verifies login before serving. Max upload size is 50 MB.

### Templates & CSS
- Base layout: `templates/base.html` — fixed sidebar + topbar + scrollable content area
- CSS custom properties in `static/css/main.css` (`--sidebar-bg`, `--accent`, etc.)
- Badge classes for WO status and priority are defined on the model (`status_class`, `priority_class`) and applied in templates
- JS in `static/js/main.js` handles dynamic task row add/remove on the job plan form
