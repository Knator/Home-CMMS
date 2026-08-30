"""PM schedule maths and the hourly generation job."""
from datetime import date, timedelta

import pytest

from app.models.pm import PM
from app.models.work_order import WorkOrder
from app.scheduler import run_pm_check
from tests.conftest import CSRF


def make_pm(db, **kwargs):
    kwargs.setdefault('name', 'Annual Heater Flush')
    kwargs.setdefault('interval_days', 365)
    kwargs.setdefault('next_due_date', date.today())
    pm = PM(**kwargs)
    db.session.add(pm)
    db.session.commit()
    return pm


def test_advance_anchors_to_due_date_not_run_date(db):
    """A late run must not shift the anniversary of every future occurrence."""
    pm = make_pm(db, next_due_date=date(2026, 1, 1), interval_days=365)
    pm.advance_schedule(on_date=date(2026, 1, 10))

    assert pm.next_due_date == date(2027, 1, 1)
    assert pm.last_generated_date == date(2026, 1, 10)


def test_advance_skips_whole_missed_intervals(db):
    """Years offline should produce one catch-up occurrence, not a backlog."""
    pm = make_pm(db, next_due_date=date(2020, 1, 1), interval_days=30)
    today = date(2026, 8, 29)
    pm.advance_schedule(on_date=today)

    assert pm.next_due_date > today
    assert pm.next_due_date - timedelta(days=30) <= today


def test_advance_tolerates_missing_interval(db):
    pm = make_pm(db, next_due_date=date(2026, 1, 1), interval_days=1)
    pm.advance_schedule(on_date=date(2026, 1, 1))
    assert pm.next_due_date == date(2026, 1, 2)


def test_scheduler_generates_one_work_order_for_a_due_pm(app, db):
    pm = make_pm(db, next_due_date=date.today())
    assert run_pm_check(app) == 1

    wo = WorkOrder.query.filter_by(pm_id=pm.id).one()
    assert wo.wo_type == 'planned'
    assert wo.status == 'open'
    assert wo.due_date == date.today()
    assert pm.last_generated_date == date.today()


def test_scheduler_is_idempotent_within_a_day(app, db):
    make_pm(db, next_due_date=date.today())
    assert run_pm_check(app) == 1
    assert run_pm_check(app) == 0
    assert WorkOrder.query.count() == 1


def test_scheduler_skips_inactive_pms(app, db):
    make_pm(db, next_due_date=date.today(), is_active=False)
    assert run_pm_check(app) == 0
    assert WorkOrder.query.count() == 0


def test_scheduler_skips_future_pms(app, db):
    make_pm(db, next_due_date=date.today() + timedelta(days=1))
    assert run_pm_check(app) == 0


def test_scheduler_picks_up_a_brand_new_pm(app, db):
    """last_generated_date IS NULL must still match the due filter."""
    pm = make_pm(db, next_due_date=date.today() - timedelta(days=5))
    assert pm.last_generated_date is None
    assert run_pm_check(app) == 1


def test_one_failing_pm_does_not_lose_the_others(app, db, monkeypatch):
    make_pm(db, name='Good one', next_due_date=date.today())
    make_pm(db, name='Bad one', next_due_date=date.today())

    # run_pm_check imports the helper at call time, so patch it at the source.
    import app.services as services
    real = services.generate_work_order_for_pm

    def flaky(pm, **kwargs):
        if pm.name == 'Bad one':
            raise RuntimeError('boom')
        return real(pm, **kwargs)

    monkeypatch.setattr(services, 'generate_work_order_for_pm', flaky)
    assert run_pm_check(app) == 1
    assert WorkOrder.query.count() == 1


def test_manual_generation_advances_the_schedule(client, db, user, login):
    # Relative to today, so the assertion cannot drift into a boundary case as
    # the calendar moves: one interval from `due` must still land in the future.
    due = date.today() - timedelta(days=10)
    pm = make_pm(db, next_due_date=due, interval_days=90)
    login()
    response = client.post(f'/pms/{pm.id}/generate', data={'csrf_token': CSRF})

    assert response.status_code == 302
    assert WorkOrder.query.filter_by(pm_id=pm.id).count() == 1
    # Anchored to the old due date, not to today.
    assert pm.next_due_date == due + timedelta(days=90)


def test_next_due_is_always_in_the_future(client, db, user, login):
    """Whatever the interval, generating must leave the PM due later than today."""
    pm = make_pm(db, next_due_date=date.today() - timedelta(days=400), interval_days=30)
    login()
    client.post(f'/pms/{pm.id}/generate', data={'csrf_token': CSRF})
    assert pm.next_due_date > date.today()


def test_manual_generation_refused_for_inactive_pm(client, db, user, login):
    pm = make_pm(db, is_active=False)
    login()
    client.post(f'/pms/{pm.id}/generate', data={'csrf_token': CSRF})
    assert WorkOrder.query.filter_by(pm_id=pm.id).count() == 0
