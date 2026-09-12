"""Two PM rules that change what an unfinished or cancelled work order means.

Without them a PM raises its next work order on schedule whether or not the
last was ever touched, so several pile up against one job; and cancelling
leaves the schedule exactly where generation put it.
"""
from datetime import date, timedelta

import pytest

from app import settings as app_settings
from app.extensions import db as _db
from app.models.pm import PM
from app.models.work_order import WorkOrder
from app.scheduler import run_pm_check
from app.services import generate_work_order_for_pm, sync_pm_schedule
from app.utils import utcnow
from tests.conftest import CSRF, make_user, prime_csrf


@pytest.fixture
def admin_client(client, db, login):
    make_user('admin', role='admin', password='Password123!')
    login('admin', 'Password123!')
    prime_csrf(client)
    return client


def make_pm(floating=False, due=None, interval=30):
    pm = PM(name='Filter change', interval_days=interval,
            next_due_date=due or date.today(),
            schedule_from_completion=floating)
    _db.session.add(pm)
    _db.session.commit()
    return pm


def next_day(pm):
    """Put the PM in the state a later scheduler run would find it in.

    run_pm_check skips any PM that already generated today — a guard that
    predates this feature and has nothing to do with stalling. Clearing the
    stamp is how a test observes the following day's run.
    """
    pm.last_generated_date = None
    pm.next_due_date = date.today()
    _db.session.commit()


def set_rules(app, stall=False, cancel_restarts=False):
    with app.test_request_context():
        app_settings.set_value('pm_stall_on_open', stall)
        app_settings.set_value('pm_cancel_restarts_clock', cancel_restarts)
        _db.session.commit()


# ── the behaviour that exists today, unchanged by default ──────────────────

def test_stalling_is_on_by_default(app, db):
    """A PM raising a second work order while the first is still open produces
    duplicates for one job, which is rarely what anyone wants."""
    with app.test_request_context():
        assert app_settings.get('pm_stall_on_open') is True


def test_switching_it_off_restores_the_old_behaviour(app, db):
    """Off, a PM generates regardless and duplicates accumulate."""
    set_rules(app, stall=False)
    pm = make_pm()
    generate_work_order_for_pm(pm)
    generate_work_order_for_pm(pm, on_date=pm.next_due_date)
    assert WorkOrder.query.filter_by(pm_id=pm.id).count() == 2


# ── stalling ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('status', ['open', 'in_progress', 'on_hold'])
def test_an_unfinished_work_order_stalls_the_pm(app, db, status):
    """On hold counts: the job is not done, so raising another helps nobody."""
    set_rules(app, stall=True)
    pm = make_pm(due=date.today() - timedelta(days=1))
    wo = generate_work_order_for_pm(pm)
    wo.status = status
    _db.session.commit()

    next_day(pm)
    generated = run_pm_check(app)

    assert generated == 0
    assert WorkOrder.query.filter_by(pm_id=pm.id).count() == 1


@pytest.mark.parametrize('status', ['completed', 'cancelled'])
def test_a_finished_work_order_does_not_stall_it(app, db, status):
    set_rules(app, stall=True)
    pm = make_pm(due=date.today() - timedelta(days=1))
    wo = generate_work_order_for_pm(pm)
    wo.status = status
    if status == 'completed':
        wo.completed_date = date.today()
    _db.session.commit()

    next_day(pm)
    run_pm_check(app)

    assert WorkOrder.query.filter_by(pm_id=pm.id).count() == 2


def test_a_stalled_pm_keeps_its_due_date(app, db):
    """The occurrence must not be quietly consumed: the PM stays due, reads as
    overdue, and generates the moment the blocker is closed."""
    set_rules(app, stall=True)
    pm = make_pm(due=date.today() - timedelta(days=5))
    generate_work_order_for_pm(pm)
    pm.next_due_date = date.today() - timedelta(days=2)
    _db.session.commit()

    due_before = pm.next_due_date
    run_pm_check(app)
    assert pm.next_due_date == due_before


def test_it_generates_as_soon_as_the_blocker_is_closed(app, db):
    set_rules(app, stall=True)
    pm = make_pm(due=date.today() - timedelta(days=1))
    wo = generate_work_order_for_pm(pm)
    next_day(pm)
    assert run_pm_check(app) == 0          # held back

    wo.status = 'cancelled'
    _db.session.commit()
    next_day(pm)
    assert run_pm_check(app) == 1          # released


def test_an_archived_work_order_never_blocks(app, db):
    """Archiving happens only after completion or cancellation, so an archived
    work order is finished by definition."""
    from app.services import archive_work_order
    set_rules(app, stall=True)
    pm = make_pm(due=date.today() - timedelta(days=1))
    wo = generate_work_order_for_pm(pm)
    wo.status = 'completed'
    wo.completed_date = date.today()
    _db.session.commit()
    archive_work_order(wo)

    next_day(pm)
    assert run_pm_check(app) == 1


def test_generate_now_still_works_but_says_what_it_noticed(admin_client, app, db):
    """An explicit instruction from an admin looking at the open work order.
    Refusing would make the button a lie."""
    set_rules(app, stall=True)
    pm = make_pm()
    blocker = generate_work_order_for_pm(pm)

    response = admin_client.post(f'/pms/{pm.id}/generate',
                                 data={'csrf_token': CSRF}, follow_redirects=True)
    assert WorkOrder.query.filter_by(pm_id=pm.id).count() == 2
    assert blocker.wo_number.encode() in response.data
    assert b'was already open' in response.data


def test_the_pm_page_explains_why_nothing_is_generating(admin_client, app, db):
    """An overdue PM that is deliberately waiting must say so, or it looks like
    the scheduler has stopped."""
    set_rules(app, stall=True)
    pm = make_pm(due=date.today() - timedelta(days=3))
    blocker = generate_work_order_for_pm(pm)
    pm.next_due_date = date.today() - timedelta(days=1)
    _db.session.commit()

    html = admin_client.get(f'/pms/{pm.id}').get_data(as_text=True)
    assert 'Waiting on' in html
    assert blocker.wo_number in html


# ── cancellation restarting a floating clock ───────────────────────────────

def test_cancelling_restarts_a_floating_pm_from_the_cancellation(app, db):
    set_rules(app, cancel_restarts=True)
    pm = make_pm(floating=True, due=date.today() - timedelta(days=10), interval=30)
    wo = generate_work_order_for_pm(pm)

    wo.status = 'cancelled'
    wo.status_changed_at = utcnow()
    _db.session.commit()
    sync_pm_schedule(wo)
    _db.session.commit()

    assert pm.next_due_date == date.today() + timedelta(days=30)


def test_a_fixed_pm_keeps_its_anniversary_when_cancelled(app, db):
    """The whole reason for choosing fixed: re-anchoring would walk the date
    forward every time something was cancelled."""
    set_rules(app, cancel_restarts=True)
    due = date.today() - timedelta(days=10)
    pm = make_pm(floating=False, due=due, interval=30)
    wo = generate_work_order_for_pm(pm)
    after_generation = pm.next_due_date

    wo.status = 'cancelled'
    wo.status_changed_at = utcnow()
    _db.session.commit()
    sync_pm_schedule(wo)
    _db.session.commit()

    assert pm.next_due_date == after_generation == due + timedelta(days=30)


def test_without_the_setting_cancelling_changes_nothing(app, db):
    set_rules(app, cancel_restarts=False)
    pm = make_pm(floating=True, due=date.today() - timedelta(days=10))
    wo = generate_work_order_for_pm(pm)
    after_generation = pm.next_due_date

    wo.status = 'cancelled'
    wo.status_changed_at = utcnow()
    _db.session.commit()
    sync_pm_schedule(wo)
    _db.session.commit()

    assert pm.next_due_date == after_generation


def test_completion_still_wins_over_an_earlier_cancellation(app, db):
    """The latest closure anchors it, so out-of-order edits cannot drag the
    schedule backwards."""
    set_rules(app, cancel_restarts=True)
    pm = make_pm(floating=True, due=date.today() - timedelta(days=20), interval=30)
    old = generate_work_order_for_pm(pm)
    old.status = 'cancelled'
    old.status_changed_at = utcnow() - timedelta(days=10)
    _db.session.commit()

    recent = generate_work_order_for_pm(pm, on_date=pm.next_due_date)
    recent.status = 'completed'
    recent.completed_date = date.today()
    _db.session.commit()

    sync_pm_schedule(recent)
    _db.session.commit()
    assert pm.next_due_date == date.today() + timedelta(days=30)


def test_repeating_the_calculation_changes_nothing(app, db):
    set_rules(app, cancel_restarts=True)
    pm = make_pm(floating=True, due=date.today() - timedelta(days=10))
    wo = generate_work_order_for_pm(pm)
    wo.status = 'cancelled'
    wo.status_changed_at = utcnow()
    _db.session.commit()

    sync_pm_schedule(wo); _db.session.commit()
    once = pm.next_due_date
    sync_pm_schedule(wo); _db.session.commit()
    assert pm.next_due_date == once
