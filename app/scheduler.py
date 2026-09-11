import atexit
import logging
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger(__name__)

# Upper bound on a PM's generate-ahead window; also the cap the form enforces.
MAX_LEAD_DAYS = 365


def run_pm_check(app):
    """Generate work orders for every PM that has come due."""
    with app.app_context():
        from app.extensions import db
        from app.models.pm import PM
        from app.services import generate_work_order_for_pm

        today = date.today()
        # Coarse SQL filter, then the exact per-PM lead time in Python — the
        # lead is a column, so `next_due_date - lead <= today` is awkward to
        # express portably in SQL and there are few enough PMs for it not to
        # matter.
        #
        # NULL last_generated_date (brand-new PM) must still match, so guard the
        # "already generated today" check with an explicit IS NULL — SQL treats
        # `NULL != today` as NULL, which would silently exclude new PMs.
        candidates = PM.query.filter(
            PM.is_active.is_(True),
            PM.next_due_date <= today + timedelta(days=MAX_LEAD_DAYS),
            db.or_(PM.last_generated_date.is_(None), PM.last_generated_date != today),
        ).all()
        due_pms = [pm for pm in candidates if pm.is_due_for_generation(today)]

        # An unfinished work order holds its PM back, when that is switched on.
        # The due date is deliberately NOT advanced for a stalled PM: advancing
        # it would quietly consume the occurrence, so instead the PM stays due,
        # reads as overdue, and generates the moment the blocker is closed.
        from app import settings as app_settings

        if app_settings.get('pm_stall_on_open'):
            unstalled = []
            for pm in due_pms:
                blocker = pm.blocking_work_order()
                if blocker is None:
                    unstalled.append(pm)
                else:
                    log.info('PM %s (%s) held back: %s is still %s',
                             pm.id, pm.name, blocker.wo_number, blocker.status)
            due_pms = unstalled

        generated = 0
        for pm in due_pms:
            # Each PM commits on its own so one failure cannot discard the work
            # orders already generated in this pass.
            try:
                wo = generate_work_order_for_pm(pm, on_date=today)
                generated += 1
                log.info("PM %s (%s) generated work order %s", pm.id, pm.name, wo.wo_number)
            except Exception:
                db.session.rollback()
                log.exception("Failed to generate a work order for PM %s (%s)", pm.id, pm.name)

        if generated:
            log.info("PM check generated %d work order(s)", generated)
        return generated


def run_auto_archive(app):
    """Archive work orders that have sat closed for longer than the setting.

    Cheap when switched off: the service reads one cached preference and
    returns, without touching the work order table at all.
    """
    with app.app_context():
        from app.services import auto_archive_closed_work_orders
        try:
            return auto_archive_closed_work_orders()
        except Exception:
            log.exception('Auto-archive pass failed')
            return 0


def start_scheduler(app):
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        run_pm_check, 'interval', hours=1, args=[app], id='pm_check',
        replace_existing=True,
        # If the process was busy or asleep, run once on resume rather than
        # firing every missed hour.
        coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    # Registered whether or not auto-archiving is switched on, because the
    # setting can change while the app runs and turning it on should not need a
    # restart. The job itself returns immediately when it is off.
    scheduler.add_job(
        run_auto_archive, 'interval', hours=1, args=[app], id='auto_archive',
        replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))
    # Kept on the app so the maintenance page can report the next run time.
    app.extensions['pm_scheduler'] = scheduler
    log.info("Scheduler started (hourly: PM check, auto-archive)")
    return scheduler
