from datetime import date, timedelta
from flask import render_template
from flask_login import login_required
from app.main import bp
from app.models.work_order import WorkOrder
from app.models.pm import PM
from app.models.asset import Asset


@bp.route('/')
@login_required
def dashboard():
    today = date.today()
    soon = today + timedelta(days=30)

    open_wo_count = WorkOrder.query.filter(WorkOrder.status.in_(['open', 'in_progress'])).count()
    overdue_count = WorkOrder.query.filter(
        WorkOrder.status.in_(['open', 'in_progress']),
        WorkOrder.due_date < today,
        WorkOrder.due_date.isnot(None),
    ).count()
    pms_due_count = PM.query.filter(
        PM.is_active == True,
        PM.next_due_date <= soon,
    ).count()
    asset_count = Asset.query.count()

    recent_wos = WorkOrder.query.order_by(WorkOrder.created_at.desc()).limit(10).all()
    pms_due_soon = (
        PM.query
        .filter(PM.is_active == True, PM.next_due_date <= soon)
        .order_by(PM.next_due_date)
        .limit(10)
        .all()
    )

    return render_template(
        'main/dashboard.html',
        open_wo_count=open_wo_count,
        overdue_count=overdue_count,
        pms_due_count=pms_due_count,
        asset_count=asset_count,
        recent_wos=recent_wos,
        pms_due_soon=pms_due_soon,
        today=today,
    )
