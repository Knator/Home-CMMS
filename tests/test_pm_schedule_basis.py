"""Fixed vs floating PM scheduling ("Schedule Based on Last Completed")."""
from datetime import date, timedelta

import pytest

from app.models.pm import PM
from app.models.work_order import WorkOrder
from app.services import create_work_order, generate_work_order_for_pm
from tests.conftest import CSRF


def make_pm(db, from_completion=False, interval=30, due=None):
    pm = PM(name='Filter change', interval_days=interval,
            next_due_date=due or date.today(),
            schedule_from_completion=from_completion)
    db.session.add(pm)
    db.session.commit()
    return pm


# ── the flag itself ────────────────────────────────────────────────────────

def test_defaults_to_a_fixed_schedule(db):
    assert make_pm(db).schedule_from_completion is False


def test_schedule_basis_label(db):
    assert make_pm(db).schedule_basis == 'Fixed interval'
    assert make_pm(db, from_completion=True).schedule_basis == 'Last completion'


def test_checkbox_round_trips_through_the_form(client, db, user, login):
    login()
    client.post('/pms/new', data={
        'name': 'Floating', 'interval_days': '30',
        'next_due_date': date.today().isoformat(),
        'schedule_from_completion': '1', 'csrf_token': CSRF})
    assert PM.query.filter_by(name='Floating').one().schedule_from_completion is True


def test_unchecked_means_fixed(client, db, user, login):
    login()
    client.post('/pms/new', data={
        'name': 'Fixed', 'interval_days': '30',
        'next_due_date': date.today().isoformat(), 'csrf_token': CSRF})
    assert PM.query.filter_by(name='Fixed').one().schedule_from_completion is False


def test_the_flag_can_be_turned_off_again(client, db, user, login):
    pm = make_pm(db, from_completion=True)
    login()
    client.post(f'/pms/{pm.id}/edit', data={
        'name': pm.name, 'interval_days': '30',
        'next_due_date': pm.next_due_date.isoformat(),
        'is_active': '1', 'csrf_token': CSRF})
    assert pm.schedule_from_completion is False


# ── fixed schedules ignore completion ──────────────────────────────────────

def test_fixed_schedule_is_unmoved_by_completion(client, db, user, login):
    due = date.today()
    pm = make_pm(db, from_completion=False, interval=30, due=due)
    wo = generate_work_order_for_pm(pm)
    after_generation = pm.next_due_date

    login()
    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': wo.title, 'status': 'completed',
        'completed_date': (due + timedelta(days=10)).isoformat(),
        'pm_id': pm.id, 'csrf_token': CSRF})

    assert pm.next_due_date == after_generation


# ── floating schedules follow completion ───────────────────────────────────

def test_floating_schedule_counts_from_the_completion_date(client, db, user, login):
    due = date.today()
    pm = make_pm(db, from_completion=True, interval=30, due=due)
    wo = generate_work_order_for_pm(pm)
    completed_on = due + timedelta(days=10)

    login()
    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': wo.title, 'status': 'completed',
        'completed_date': completed_on.isoformat(), 'csrf_token': CSRF})

    assert pm.next_due_date == completed_on + timedelta(days=30)


def test_finishing_late_pushes_the_cycle_out(client, db, user, login):
    """The whole point of the option: a late job moves everything after it.

    The due date is offset from the interval grid on purpose — completing
    exactly on the grid gives both modes the same answer and proves nothing.
    """
    due = date.today() - timedelta(days=5)
    fixed = make_pm(db, from_completion=False, interval=30, due=due)
    floating = make_pm(db, from_completion=True, interval=30, due=due)
    fixed_wo = generate_work_order_for_pm(fixed)
    floating_wo = generate_work_order_for_pm(floating)

    login()
    late = date.today()
    for wo in (fixed_wo, floating_wo):
        client.post(f'/work-orders/{wo.id}/edit', data={
            'title': wo.title, 'status': 'completed',
            'completed_date': late.isoformat(), 'csrf_token': CSRF})

    # Fixed keeps its rhythm: 30 days after the previous due date.
    assert fixed.next_due_date == due + timedelta(days=30)
    # Floating restarts the clock at the completion.
    assert floating.next_due_date == late + timedelta(days=30)
    assert fixed.next_due_date < floating.next_due_date


def test_completing_out_of_order_does_not_drag_the_schedule_back(client, db, user, login):
    """The newest completion wins, not whichever was saved last."""
    pm = make_pm(db, from_completion=True, interval=30)
    recent = create_work_order(title='Recent', pm_id=pm.id)
    older = create_work_order(title='Older', pm_id=pm.id)

    login()
    client.post(f'/work-orders/{recent.id}/edit', data={
        'title': 'Recent', 'status': 'completed',
        'completed_date': '2026-06-01', 'csrf_token': CSRF})
    assert pm.next_due_date == date(2026, 6, 1) + timedelta(days=30)

    client.post(f'/work-orders/{older.id}/edit', data={
        'title': 'Older', 'status': 'completed',
        'completed_date': '2026-01-01', 'csrf_token': CSRF})
    assert pm.next_due_date == date(2026, 6, 1) + timedelta(days=30)


def test_recalculating_twice_changes_nothing(db):
    pm = make_pm(db, from_completion=True, interval=30)
    create_work_order(title='Done', pm_id=pm.id, completed_date=date(2026, 5, 1))

    assert pm.reschedule_from_completion() is True
    first = pm.next_due_date
    assert pm.reschedule_from_completion() is False
    assert pm.next_due_date == first


def test_no_completions_leaves_the_schedule_alone(db):
    pm = make_pm(db, from_completion=True, interval=30)
    create_work_order(title='Open', pm_id=pm.id)
    original = pm.next_due_date

    assert pm.reschedule_from_completion() is False
    assert pm.next_due_date == original


def test_other_pms_work_orders_are_ignored(db):
    mine = make_pm(db, from_completion=True, interval=30)
    theirs = make_pm(db, from_completion=True, interval=30)
    create_work_order(title='Theirs', pm_id=theirs.id, completed_date=date(2026, 9, 9))

    assert mine.reschedule_from_completion() is False


def test_a_work_order_with_no_pm_is_harmless(client, db, user, login):
    login()
    wo = create_work_order(title='Standalone')
    response = client.post(f'/work-orders/{wo.id}/edit', data={
        'title': 'Standalone', 'status': 'completed',
        'completed_date': date.today().isoformat(), 'csrf_token': CSRF})
    assert response.status_code == 302


def test_creating_a_completed_wo_against_a_pm_reschedules(client, db, user, login):
    pm = make_pm(db, from_completion=True, interval=14)
    login()
    completed_on = date(2026, 7, 4)
    wo = create_work_order(title='Logged after the fact', pm_id=pm.id,
                           completed_date=completed_on)
    from app.services import sync_pm_schedule
    sync_pm_schedule(wo)

    assert pm.next_due_date == completed_on + timedelta(days=14)


def test_turning_the_flag_on_reschedules_immediately(client, db, user, login):
    """Switching to floating should take effect now, not at the next completion."""
    pm = make_pm(db, from_completion=False, interval=30)
    create_work_order(title='Done', pm_id=pm.id, completed_date=date(2026, 4, 1))

    login()
    client.post(f'/pms/{pm.id}/edit', data={
        'name': pm.name, 'interval_days': '30',
        'next_due_date': pm.next_due_date.isoformat(),
        'schedule_from_completion': '1', 'is_active': '1', 'csrf_token': CSRF})

    assert pm.next_due_date == date(2026, 4, 1) + timedelta(days=30)


# ── surfaced in the UI ─────────────────────────────────────────────────────

def test_basis_is_shown_on_detail_and_list(client, db, user, login):
    make_pm(db, from_completion=True)
    login()
    pm = PM.query.one()

    assert 'Last completion' in client.get(f'/pms/{pm.id}').get_data(as_text=True)
    listing = client.get('/pms/').get_data(as_text=True)
    assert '<th>Basis</th>' in listing
    assert 'Last completion' in listing


def test_form_offers_the_checkbox(client, db, user, login):
    login()
    body = client.get('/pms/new').get_data(as_text=True)
    assert 'name="schedule_from_completion"' in body
    assert 'Schedule Based on Last Completed' in body
