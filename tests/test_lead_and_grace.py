"""Generate-ahead lead time on PMs, and overdue grace on PMs and work orders."""
from datetime import date, timedelta

import pytest

from app.models.pm import PM
from app.models.work_order import WorkOrder
from app.scheduler import run_pm_check
from app.services import create_work_order, generate_work_order_for_pm
from tests.conftest import CSRF


def make_pm(db, lead=0, grace=0, interval=30, due=None, active=True):
    pm = PM(name='Filter change', interval_days=interval,
            next_due_date=due or date.today(), generate_lead_days=lead,
            overdue_grace_days=grace, is_active=active)
    db.session.add(pm)
    db.session.commit()
    return pm


# ── lead time ──────────────────────────────────────────────────────────────

def test_defaults_to_no_lead_or_grace(db):
    pm = make_pm(db)
    assert pm.generate_lead_days == 0
    assert pm.overdue_grace_days == 0


def test_generation_date_is_the_due_date_minus_the_lead(db):
    due = date.today() + timedelta(days=30)
    pm = make_pm(db, lead=7, due=due)
    assert pm.generation_date == due - timedelta(days=7)


def test_scheduler_generates_early_within_the_lead_window(app, db):
    make_pm(db, lead=7, due=date.today() + timedelta(days=5))
    assert run_pm_check(app) == 1


def test_scheduler_waits_until_the_window_opens(app, db):
    make_pm(db, lead=7, due=date.today() + timedelta(days=8))
    assert run_pm_check(app) == 0


def test_the_window_opens_exactly_on_the_boundary(app, db):
    make_pm(db, lead=7, due=date.today() + timedelta(days=7))
    assert run_pm_check(app) == 1


def test_no_lead_still_waits_for_the_due_date(app, db):
    make_pm(db, lead=0, due=date.today() + timedelta(days=1))
    assert run_pm_check(app) == 0


def test_an_early_work_order_keeps_the_real_due_date(app, db):
    due = date.today() + timedelta(days=5)
    pm = make_pm(db, lead=7, due=due)
    run_pm_check(app)

    wo = WorkOrder.query.filter_by(pm_id=pm.id).one()
    assert wo.due_date == due          # generated early, still due when due


def test_generating_early_does_not_regenerate_next_day(app, db):
    """A lead shorter than the interval must not loop."""
    make_pm(db, lead=7, interval=30, due=date.today() + timedelta(days=5))
    assert run_pm_check(app) == 1

    pm = PM.query.one()
    pm.last_generated_date = date.today() - timedelta(days=1)   # pretend a day passed
    db.session.commit()
    assert run_pm_check(app) == 0


def test_inactive_pms_are_still_skipped(app, db):
    make_pm(db, lead=30, due=date.today() + timedelta(days=5), active=False)
    assert run_pm_check(app) == 0


def test_lead_must_be_less_than_the_interval(client, db, user, login):
    """A lead at or above the interval would make the PM generate every day."""
    login()
    response = client.post('/pms/new', data={
        'name': 'Runaway', 'interval_days': '30', 'generate_lead_days': '30',
        'next_due_date': date.today().isoformat(), 'csrf_token': CSRF})

    assert response.status_code == 200
    assert 'less than the interval' in response.get_data(as_text=True)
    assert PM.query.count() == 0


def test_lead_is_capped(client, db, user, login):
    login()
    response = client.post('/pms/new', data={
        'name': 'Silly', 'interval_days': '4000', 'generate_lead_days': '900',
        'next_due_date': date.today().isoformat(), 'csrf_token': CSRF})
    assert response.status_code == 200
    assert PM.query.count() == 0


def test_lead_round_trips_through_the_form(client, db, user, login):
    login()
    client.post('/pms/new', data={
        'name': 'Early bird', 'interval_days': '90', 'generate_lead_days': '14',
        'overdue_grace_days': '3', 'next_due_date': date.today().isoformat(),
        'csrf_token': CSRF})
    pm = PM.query.one()
    assert (pm.generate_lead_days, pm.overdue_grace_days) == (14, 3)


# ── overdue grace ──────────────────────────────────────────────────────────

def test_work_order_is_overdue_the_day_after_due_by_default(db):
    yesterday = date.today() - timedelta(days=1)
    assert create_work_order(title='Late', due_date=yesterday).is_overdue is True


def test_grace_delays_the_overdue_flag(db):
    due = date.today() - timedelta(days=3)
    assert create_work_order(title='In grace', due_date=due, overdue_grace_days=5).is_overdue is False


def test_overdue_once_the_grace_expires(db):
    due = date.today() - timedelta(days=6)
    assert create_work_order(title='Past grace', due_date=due, overdue_grace_days=5).is_overdue is True


def test_grace_boundary_is_inclusive(db):
    """With 5 days' grace, day 5 is still fine and day 6 is late."""
    on_grace = create_work_order(title='Day five',
                                 due_date=date.today() - timedelta(days=5),
                                 overdue_grace_days=5)
    past = create_work_order(title='Day six',
                             due_date=date.today() - timedelta(days=6),
                             overdue_grace_days=5)
    assert on_grace.is_overdue is False
    assert past.is_overdue is True


def test_completed_work_is_never_overdue(db):
    wo = create_work_order(title='Done', due_date=date.today() - timedelta(days=99),
                           status='completed')
    assert wo.is_overdue is False


def test_a_work_order_with_no_due_date_is_never_overdue(db):
    assert create_work_order(title='No date').is_overdue is False


def test_pm_grace_delays_its_overdue_flag(db):
    pm = make_pm(db, grace=5, due=date.today() - timedelta(days=3))
    assert pm.is_overdue is False

    pm.next_due_date = date.today() - timedelta(days=6)
    assert pm.is_overdue is True


def test_generated_work_orders_inherit_the_pms_grace(db):
    pm = make_pm(db, grace=10)
    wo = generate_work_order_for_pm(pm)
    assert wo.overdue_grace_days == 10


def test_grace_round_trips_on_the_work_order_form(client, db, user, login):
    login()
    client.post('/work-orders/new', data={
        'title': 'Graceful', 'status': 'open', 'overdue_grace_days': '4',
        'csrf_token': CSRF}, content_type='multipart/form-data')
    assert WorkOrder.query.one().overdue_grace_days == 4


def test_dashboard_overdue_count_respects_grace(client, db, user, login):
    create_work_order(title='Genuinely late', due_date=date.today() - timedelta(days=10))
    create_work_order(title='Still in grace', due_date=date.today() - timedelta(days=2),
                      overdue_grace_days=30)

    login()
    body = client.get('/').get_data(as_text=True)
    # One of the two is overdue; the stat card must agree with the row styling.
    assert body.count('overdue-row') == 1
