from datetime import date, timedelta
from flask import render_template
from flask_login import login_required
from app.main import bp
from app.models.work_order import WorkOrder
from app.models.pm import PM


@bp.route('/')
@login_required
def dashboard():
    today = date.today()
    soon = today + timedelta(days=30)

    # On hold counts as open: the job is not done. Leaving it out made a
    # parked work order invisible on this page while it still blocked its PM
    # from generating another — see PM.blocking_work_order, which has always
    # treated on hold as unfinished.
    open_wo_count = WorkOrder.query.filter(
        WorkOrder.status.in_(['open', 'in_progress', 'on_hold'])).count()
    # Grace periods are per-record, so the count is settled in Python using the
    # same is_overdue the pages display. `due_date < today` is a superset of
    # what can be overdue, so it stays a cheap SQL prefilter.
    overdue_count = sum(
        1 for wo in WorkOrder.query.filter(
            WorkOrder.status.in_(['open', 'in_progress']),
            WorkOrder.due_date < today,
            WorkOrder.due_date.isnot(None),
        ).all()
        if wo.is_overdue
    )
    pms_due_count = PM.query.filter(
        PM.is_active.is_(True),
        PM.next_due_date <= soon,
    ).count()
    # Work finished recently — the one backward-looking number on the page, and
    # the only sign that the system is being used rather than just accruing
    # overdue rows.
    #
    # Archived work orders are counted. Archiving is a filing decision, not an
    # undoing: leaving them out would make this number fall when somebody tidies
    # up, which reads as a bug. That is a deliberate departure from the rest of
    # the dashboard, which hides archived records from *listings*.
    completed_count = WorkOrder.query.filter(
        WorkOrder.status == 'completed',
        WorkOrder.completed_date.isnot(None),
        WorkOrder.completed_date >= today - timedelta(days=30),
    ).count()

    recent_wos = (
        WorkOrder.query
        .filter(WorkOrder.archived_at.is_(None))
        .order_by(WorkOrder.created_at.desc())
        .limit(10)
        .all()
    )
    pms_due_soon = (
        PM.query
        .filter(PM.is_active.is_(True), PM.next_due_date <= soon)
        .order_by(PM.next_due_date)
        .limit(10)
        .all()
    )

    return render_template(
        'main/dashboard.html',
        open_wo_count=open_wo_count,
        overdue_count=overdue_count,
        pms_due_count=pms_due_count,
        completed_count=completed_count,
        recent_wos=recent_wos,
        pms_due_soon=pms_due_soon,
        today=today,
    )
