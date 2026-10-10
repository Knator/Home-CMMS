"""user ids are never reused

The users table becomes AUTOINCREMENT. Without it SQLite gives a new row
max(id) + 1, so once accounts can be deleted, deleting the newest and creating
another would hand the new person the old id. Session and remember-me cookies
carry nothing but the id, so a browser still signed in as the deleted account
would find itself signed in as the new one.

SQLite cannot add AUTOINCREMENT to an existing table, so it is rebuilt from an
explicit definition: rows are copied across with their ids unchanged, which
keeps every session valid and every reference from work orders, PMs, job plans,
attachments, settings and API tokens pointing at the same person. Copying rows
with explicit ids sets SQLite's sequence to the highest of them, so numbering
carries on from there. migrations/env.py switches foreign key enforcement off
for the rebuild, as it must for any table other tables point at.

Revision ID: c7a2e4f19b38
Revises: b3c1d7e9f2a4
Create Date: 2026-10-09 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c7a2e4f19b38'
down_revision = 'b3c1d7e9f2a4'
branch_labels = None
depends_on = None


def _users_table(autoincrement):
    return sa.Table(
        'users', sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.Column('display_name', sa.String(length=120), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username'),
        sa.UniqueConstraint('email'),
        sqlite_autoincrement=autoincrement,
    )


def _rebuild(autoincrement):
    with op.batch_alter_table('users', copy_from=_users_table(autoincrement),
                              recreate='always',
                              table_kwargs={'sqlite_autoincrement': autoincrement}):
        pass


def upgrade():
    _rebuild(True)


def downgrade():
    _rebuild(False)
