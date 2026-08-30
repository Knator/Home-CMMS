"""per-parent location names and asset numbers

Two changes, both needing hand-written SQL rather than autogenerate:

1. locations.name loses its global UNIQUE and gains a unique index scoped to the
   parent, so "Basement > Bathroom" and "Main Floor > Bathroom" can coexist.
   COALESCE is required because SQL treats NULLs as distinct, which would let two
   top-level locations share a name; lower() makes it case-insensitive.

2. assets gains a stable AST-NNNNN number, backfilled from the row id so existing
   assets keep a number that matches their creation order.

Revision ID: fa96c9674a53
Revises: f5cecb768064
Create Date: 2026-08-30

"""
from alembic import op
import sqlalchemy as sa


revision = 'fa96c9674a53'
down_revision = 'f5cecb768064'
branch_labels = None
depends_on = None


def _locations_table(unique_name):
    """The locations table as it should look, for SQLite batch rebuilds.

    Passing copy_from avoids reflection, which would carry the old UNIQUE across.
    """
    return sa.Table(
        'locations', sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False, unique=unique_name),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['parent_id'], ['locations.id'],
                                name='fk_locations_parent_id_locations'),
    )


def upgrade():
    # ── locations: global unique -> unique per parent ──────────────────────
    with op.batch_alter_table('locations', copy_from=_locations_table(False),
                              recreate='always') as batch_op:
        batch_op.create_index('ix_locations_parent_id', ['parent_id'], unique=False)

    op.execute(
        'CREATE UNIQUE INDEX uq_locations_parent_name '
        'ON locations (coalesce(parent_id, -1), lower(name))'
    )

    # ── assets: stable AST- number ─────────────────────────────────────────
    with op.batch_alter_table('assets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('asset_number', sa.String(length=20), nullable=True))

    # Backfill from the row id: unique by construction and stable across reruns.
    op.execute("UPDATE assets SET asset_number = 'AST-' || substr('00000' || id, -5, 5) "
               "WHERE asset_number IS NULL")

    with op.batch_alter_table('assets', schema=None) as batch_op:
        batch_op.alter_column('asset_number', existing_type=sa.String(length=20), nullable=False)
        batch_op.create_index('uq_assets_asset_number', ['asset_number'], unique=True)


def downgrade():
    with op.batch_alter_table('assets', schema=None) as batch_op:
        batch_op.drop_index('uq_assets_asset_number')
        batch_op.drop_column('asset_number')

    op.execute('DROP INDEX IF EXISTS uq_locations_parent_name')

    # Restoring the global unique fails if per-parent duplicates were created
    # while it was lifted; that is the intended signal to resolve them by hand.
    with op.batch_alter_table('locations', copy_from=_locations_table(True),
                              recreate='always') as batch_op:
        batch_op.create_index('ix_locations_parent_id', ['parent_id'], unique=False)
