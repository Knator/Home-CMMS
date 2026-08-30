import sqlite3

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'
# Invalidate a remembered session if the client fingerprint changes.
login_manager.session_protection = 'strong'


# Alembic's batch migrations rebuild each table (create temp -> copy -> drop ->
# rename). SQLite cannot do that with foreign key enforcement on, so
# migrations/env.py flips this off for the migration connection only.
_enforce_sqlite_foreign_keys = True


def set_sqlite_foreign_keys(enabled):
    global _enforce_sqlite_foreign_keys
    _enforce_sqlite_foreign_keys = enabled


@event.listens_for(Engine, 'connect')
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """SQLite needs three pragmas set per connection.

    - foreign_keys: OFF by default, so ON DELETE CASCADE (job_plan_tasks) is
      silently ignored without it.
    - journal_mode=WAL: lets the hourly PM scheduler write while requests read.
    - busy_timeout: wait for a competing writer instead of raising
      "database is locked" immediately.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=' + ('ON' if _enforce_sqlite_foreign_keys else 'OFF'))
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA busy_timeout=30000')
        cursor.close()
