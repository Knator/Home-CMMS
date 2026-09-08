"""archived becomes a flag, not a status

Revision ID: 10046c5492a5
Revises: 5dd60a8ba52a
Create Date: 2026-09-07 21:06:03.990357

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '10046c5492a5'
down_revision = '5dd60a8ba52a'
branch_labels = None
depends_on = None


def upgrade():
    """Restore the outcome that status='archived' overwrote.

    Archiving used to replace the status; it now sets archived_at and leaves the
    status alone, so 'completed' and 'cancelled' survive. Rows archived under
    the old scheme have lost that distinction, but not irrecoverably:
    completed_date is set when a work order is completed and never when it is
    cancelled, so it says which each one was.

    archived_at is already populated on these rows and is the flag, so nothing
    else needs changing.
    """
    op.execute("""
        UPDATE work_orders
           SET status = CASE WHEN completed_date IS NOT NULL
                             THEN 'completed' ELSE 'cancelled' END
         WHERE status = 'archived'
    """)


def downgrade():
    op.execute("""
        UPDATE work_orders
           SET status = 'archived'
         WHERE archived_at IS NOT NULL
    """)
