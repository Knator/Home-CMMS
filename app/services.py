"""Write paths shared by the web routes and the background scheduler."""
import logging

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.work_order import WorkOrder

log = logging.getLogger(__name__)

MAX_NUMBER_ATTEMPTS = 5


def create_work_order(**fields):
    """Insert and commit one work order, assigning the next WO number.

    WO numbers are allocated read-then-write, so a concurrent insert can claim
    the same number; the unique constraint rejects the loser and we retry with a
    freshly read number. Any other pending change in the session is committed in
    the same transaction, which is what lets a PM advance atomically with the
    work order it generated.
    """
    for attempt in range(MAX_NUMBER_ATTEMPTS):
        wo = WorkOrder(wo_number=WorkOrder.generate_wo_number(), **fields)
        db.session.add(wo)
        try:
            db.session.commit()
            return wo
        except IntegrityError:
            db.session.rollback()
            log.warning(
                "Work order number collision (attempt %d/%d), retrying",
                attempt + 1, MAX_NUMBER_ATTEMPTS,
            )
    raise RuntimeError('Could not allocate a unique work order number.')


def generate_work_order_for_pm(pm, created_by=None, description=None, on_date=None):
    """Create the PM's next work order and advance the schedule in one transaction.

    Both halves must commit together. If the work order were committed first and
    the advance failed, the PM would still look due and the next scheduler tick
    would generate a duplicate.
    """
    for attempt in range(MAX_NUMBER_ATTEMPTS):
        due_date = pm.next_due_date
        pm.advance_schedule(on_date=on_date)
        wo = WorkOrder(
            wo_number=WorkOrder.generate_wo_number(),
            title=pm.name,
            wo_type='planned',
            status='open',
            priority='medium',
            asset_id=pm.asset_id,
            location_id=pm.location_id,
            job_plan_id=pm.job_plan_id,
            pm_id=pm.id,
            due_date=due_date,
            description=description or f"Auto-generated from PM schedule: {pm.name}",
            created_by=created_by,
        )
        db.session.add(wo)
        try:
            db.session.commit()
            return wo
        except IntegrityError:
            # Rolls back the schedule advance too, so the retry recomputes it
            # from the PM's restored state.
            db.session.rollback()
            log.warning(
                "Work order number collision generating PM %s (attempt %d/%d), retrying",
                pm.id, attempt + 1, MAX_NUMBER_ATTEMPTS,
            )
    raise RuntimeError('Could not allocate a unique work order number.')
