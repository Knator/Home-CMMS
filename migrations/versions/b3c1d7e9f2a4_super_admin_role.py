"""Super admin role: existing admins become super admins

Maintenance is now for super admins only. Every admin could use it before this
revision, so every admin is promoted: nobody gains or loses anything by
upgrading, and the super admin can then demote whoever should only be an admin.
Promoting nobody instead would leave an upgraded instance with Maintenance —
backups and restore — reachable from a shell only.

Revision ID: b3c1d7e9f2a4
Revises: 84b7eef551cb
Create Date: 2026-10-09 12:00:00.000000

"""
from alembic import op


revision = 'b3c1d7e9f2a4'
down_revision = '84b7eef551cb'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET role = 'super_admin' WHERE role = 'admin'")


def downgrade():
    # Before this revision the two were one role.
    op.execute("UPDATE users SET role = 'admin' WHERE role = 'super_admin'")
