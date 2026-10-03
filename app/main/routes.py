from datetime import date, timedelta
from flask import render_template
from flask_login import login_required
from app.main import bp
from app.models.work_order import OVERDUE_SOON_DAYS as SOON_DAYS, WorkOrder
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
    # Both from the clauses on the model, which the work order list filters by
    # too — so a card's number and the list it links to cannot disagree. The
    # count used to be its own rule, open and in-progress only, while every red
    # row on the list counted on hold as well.
    overdue_count = WorkOrder.query.filter(WorkOrder.overdue_clause(today)).count()

    # Work about to go overdue, so it can be dealt with first. By the day it
    # goes overdue rather than the day it is due: with grace periods those
    # differ, and this card is about what turns red next.
    overdue_soon_count = WorkOrder.query.filter(
        WorkOrder.overdue_soon_clause(today, days=SOON_DAYS)).count()
    pms_due_count = PM.query.filter(
        PM.is_active.is_(True),
        PM.next_due_date <= soon,
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
        overdue_soon_count=overdue_soon_count, soon_days=SOON_DAYS,
        recent_wos=recent_wos,
        pms_due_soon=pms_due_soon,
        today=today,
    )
