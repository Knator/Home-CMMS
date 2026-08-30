"""work order completed date is a date

completed_date held a machine-generated timestamp. It is now user-editable and
semantically a calendar date, matching due_date, so existing values are
truncated to their day and the column type changes.

Deliberately NOT done with alter_column(type_=sa.Date()): Alembic's batch type
change emits CAST(completed_date AS DATE), and SQLite's DATE has NUMERIC
affinity, so CAST('2026-08-30' AS DATE) silently yields the integer 2026.
Rebuilding the table from an explicit definition copies the column across
without a cast, and text that is not a well-formed number keeps its text
storage class.

Revision ID: 713382d2c88b
Revises: fa96c9674a53
Create Date: 2026-08-30

"""
from alembic import op
import sqlalchemy as sa


revision = '713382d2c88b'
down_revision = 'fa96c9674a53'
branch_labels = None
depends_on = None


def _work_orders_table(completed_type):
    return sa.Table(
        'work_orders', sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('wo_number', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('wo_type', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('priority', sa.String(length=20), nullable=False),
        sa.Column('asset_id', sa.Integer(), nullable=True),
        sa.Column('location_id', sa.Integer(), nullable=True),
        sa.Column('job_plan_id', sa.Integer(), nullable=True),
        sa.Column('pm_id', sa.Integer(), nullable=True),
        sa.Column('assigned_to', sa.Integer(), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('completed_date', completed_type, nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('wo_number'),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id']),
        sa.ForeignKeyConstraint(['assigned_to'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['job_plan_id'], ['job_plans.id']),
        sa.ForeignKeyConstraint(['location_id'], ['locations.id']),
        sa.ForeignKeyConstraint(['pm_id'], ['pms.id']),
    )


def upgrade():
    op.execute("UPDATE work_orders SET completed_date = date(completed_date) "
               "WHERE completed_date IS NOT NULL")

    with op.batch_alter_table('work_orders', copy_from=_work_orders_table(sa.Date()),
                              recreate='always'):
        pass


def downgrade():
    with op.batch_alter_table('work_orders', copy_from=_work_orders_table(sa.DateTime()),
                              recreate='always'):
        pass
    # Dates widen back to midnight timestamps; the original time of day is gone.
    op.execute("UPDATE work_orders SET completed_date = datetime(completed_date) "
               "WHERE completed_date IS NOT NULL")
