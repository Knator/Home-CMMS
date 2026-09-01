"""location numbers

Gives locations a stable LOC-NNNNN identifier, matching assets and work orders.
Existing rows are backfilled from their id so numbering follows creation order.

Revision ID: 1999d973e2f0
Revises: 90f7064e9bf0
Create Date: 2026-08-31

"""
from alembic import op
import sqlalchemy as sa


revision = '1999d973e2f0'
down_revision = '90f7064e9bf0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('locations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location_number', sa.String(length=20), nullable=True))

    op.execute("UPDATE locations SET location_number = 'LOC-' || substr('00000' || id, -5, 5) "
               "WHERE location_number IS NULL")

    with op.batch_alter_table('locations', schema=None) as batch_op:
        batch_op.alter_column('location_number', existing_type=sa.String(length=20),
                              nullable=False)
        batch_op.create_index('uq_locations_location_number', ['location_number'], unique=True)


def downgrade():
    with op.batch_alter_table('locations', schema=None) as batch_op:
        batch_op.drop_index('uq_locations_location_number')
        batch_op.drop_column('location_number')
