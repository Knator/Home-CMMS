from datetime import date
from apscheduler.schedulers.background import BackgroundScheduler


def run_pm_check(app):
    with app.app_context():
        from app.extensions import db
        from app.models.pm import PM
        from app.models.work_order import WorkOrder

        today = date.today()
        # NULL last_generated_date (brand-new PM) must still match, so guard the
        # "already generated today" check with an explicit IS NULL — SQL treats
        # `NULL != today` as NULL, which would silently exclude new PMs.
        due_pms = PM.query.filter(
            PM.is_active == True,
            PM.next_due_date <= today,
            db.or_(PM.last_generated_date.is_(None), PM.last_generated_date != today),
        ).all()

        for pm in due_pms:
            wo_number = WorkOrder.generate_wo_number()
            wo = WorkOrder(
                wo_number=wo_number,
                title=pm.name,
                wo_type='planned',
                status='open',
                priority='medium',
                asset_id=pm.asset_id,
                location_id=pm.location_id,
                job_plan_id=pm.job_plan_id,
                pm_id=pm.id,
                due_date=pm.next_due_date,
                description=f"Auto-generated from PM schedule: {pm.name}",
            )
            db.session.add(wo)
            pm.advance_schedule()

        if due_pms:
            db.session.commit()


def start_scheduler(app):
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_pm_check, 'interval', hours=1, args=[app], id='pm_check', replace_existing=True)
    scheduler.start()
    return scheduler
