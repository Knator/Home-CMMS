"""Archiving closed work orders automatically after a waiting period.

The rule is "closed for longer than N days". What counts as closed is the
interesting part: completed work has a completion date, cancelled work has none,
and updated_at is no substitute because editing a note would restart the clock.
"""
from datetime import date, timedelta

import pytest

from app import settings as app_settings
from app.extensions import db as _db
from app.models.work_order import WorkOrder
from app.services import auto_archive_closed_work_orders, create_work_order
from app.utils import to_local, utcnow
from tests.conftest import CSRF, prime_csrf


@pytest.fixture
def admin_client(client, db, admin, login):
    login('admin')
    prime_csrf(client)
    return client


def enable(client, days):
    client.post('/admin/settings', data={
        'csrf_token': CSRF,
        'upload_limit_enabled': '1', 'max_upload_mb': '100',
        'allow_archived_deletion': '1',
        'auto_archive_enabled': '1', 'auto_archive_days': str(days),
    })


def closed_days_ago(days, status='completed', **kwargs):
    """A work order closed `days` ago, dated the way that status would be."""
    when = date.today() - timedelta(days=days)
    wo = create_work_order(title='Old job', wo_type='planned', status=status,
                           completed_date=when if status == 'completed' else None,
                           **kwargs)
    if status != 'completed':
        wo.status_changed_at = utcnow() - timedelta(days=days)
        _db.session.commit()
    return wo


# ── switched off ───────────────────────────────────────────────────────────

def test_nothing_happens_while_the_feature_is_off(app, db):
    old = closed_days_ago(500)
    with app.test_request_context():
        assert auto_archive_closed_work_orders() == 0
    assert not old.is_archived


def test_it_does_not_even_look_when_switched_off(app, db, monkeypatch):
    """'If the feature is off, we don't even need to run the checks.'"""
    closed_days_ago(500)
    called = []
    original = WorkOrder.query.__class__.filter

    def spy(self, *args, **kwargs):
        called.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(WorkOrder.query.__class__, 'filter', spy)
    with app.test_request_context():
        auto_archive_closed_work_orders()
    assert called == []


# ── the rule ───────────────────────────────────────────────────────────────

def test_work_closed_longer_ago_than_the_window_is_archived(admin_client, app, db):
    old = closed_days_ago(100)
    enable(admin_client, 90)
    with app.test_request_context():
        assert auto_archive_closed_work_orders() == 1
    assert old.is_archived


def test_work_inside_the_window_is_left_alone(admin_client, app, db):
    recent = closed_days_ago(10)
    enable(admin_client, 90)
    with app.test_request_context():
        assert auto_archive_closed_work_orders() == 0
    assert not recent.is_archived


def test_the_boundary_day_is_included(admin_client, app, db):
    exactly = closed_days_ago(90)
    enable(admin_client, 90)
    with app.test_request_context():
        auto_archive_closed_work_orders()
    assert exactly.is_archived


def test_cancelled_work_is_archived_too(admin_client, app, db):
    """It has no completed_date, so this is what status_changed_at is for."""
    cancelled = closed_days_ago(100, status='cancelled')
    assert cancelled.completed_date is None
    enable(admin_client, 90)
    with app.test_request_context():
        auto_archive_closed_work_orders()
    assert cancelled.is_archived


@pytest.mark.parametrize('status', ['open', 'in_progress', 'on_hold'])
def test_work_still_in_flight_is_never_touched(admin_client, app, db, status):
    live = create_work_order(title='Live', wo_type='unplanned', status=status)
    live.status_changed_at = utcnow() - timedelta(days=999)
    _db.session.commit()

    enable(admin_client, 30)
    with app.test_request_context():
        auto_archive_closed_work_orders()
    assert not live.is_archived


def test_already_archived_work_is_not_processed_again(admin_client, app, db):
    from app.services import archive_work_order
    old = closed_days_ago(100)
    archive_work_order(old)
    stamp = old.archived_at

    enable(admin_client, 90)
    with app.test_request_context():
        assert auto_archive_closed_work_orders() == 0
    assert old.archived_at == stamp


def test_zero_days_archives_as_soon_as_it_closes(admin_client, app, db):
    today = closed_days_ago(0)
    enable(admin_client, 0)
    with app.test_request_context():
        auto_archive_closed_work_orders()
    assert today.is_archived


# ── the clock is not reset by editing ──────────────────────────────────────

def test_editing_a_closed_work_order_does_not_restart_the_clock(admin_client,
                                                                app, db):
    """Why updated_at is not the reference: a note added yesterday would push
    the archive date out by the whole window."""
    old = closed_days_ago(100)
    old.notes = 'Added a comment today'
    _db.session.commit()
    # updated_at is stored UTC; today is a local calendar date. Convert before
    # comparing, or this fails for the hours when the two dates differ.
    assert to_local(old.updated_at).date() == date.today()

    enable(admin_client, 90)
    with app.test_request_context():
        auto_archive_closed_work_orders()
    assert old.is_archived


def test_the_status_stamp_moves_only_when_the_status_moves(db):
    wo = create_work_order(title='Job', wo_type='unplanned', status='open')
    first = wo.status_changed_at
    wo.title = 'Renamed'
    _db.session.commit()
    assert wo.status_changed_at == first

    wo.status = 'completed'
    _db.session.commit()
    assert wo.status_changed_at > first


# ── the snapshot still happens ─────────────────────────────────────────────

def test_auto_archived_work_is_frozen_like_any_other(admin_client, app, db):
    from app.services import create_asset
    asset = create_asset(name='Furnace')
    old = closed_days_ago(100, asset_id=asset.id)

    enable(admin_client, 90)
    with app.test_request_context():
        auto_archive_closed_work_orders()

    assert old.snapshot_value('asset_name') == 'Furnace'
    asset.name = 'Renamed'
    _db.session.commit()
    assert old.snapshot_value('asset_name') == 'Furnace'


# ── the page ───────────────────────────────────────────────────────────────

def test_run_now_archives_immediately(admin_client, db):
    old = closed_days_ago(100)
    enable(admin_client, 90)
    response = admin_client.post('/admin/settings/auto-archive/run',
                                 data={'csrf_token': CSRF}, follow_redirects=True)
    assert b'Archived 1' in response.data
    assert old.is_archived


def test_run_now_is_refused_while_the_feature_is_off(admin_client, db):
    old = closed_days_ago(100)
    response = admin_client.post('/admin/settings/auto-archive/run',
                                 data={'csrf_token': CSRF}, follow_redirects=True)
    assert b'switched off' in response.data
    assert not old.is_archived


def test_run_now_is_admin_only(client, db, user, login):
    login()
    prime_csrf(client)
    assert client.post('/admin/settings/auto-archive/run',
                       data={'csrf_token': CSRF}).status_code in (302, 403)


def test_enabling_without_a_day_count_is_refused(admin_client, app):
    response = admin_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '100',
        'auto_archive_enabled': '1', 'auto_archive_days': 'soon',
    })
    assert b'how many days' in response.data
    with app.test_request_context():
        assert app_settings.get('auto_archive_enabled') is False


def test_the_detail_page_shows_when_the_status_last_changed(admin_client, db):
    """Read-only: it is stamped by the app, and auto-archiving counts from it
    for work with no completion date."""
    wo = create_work_order(title='Job', wo_type='unplanned', status='open')
    html = admin_client.get(f'/work-orders/{wo.id}').get_data(as_text=True)
    assert 'Status Changed' in html

    # It is shown, not editable.
    form = admin_client.get(f'/work-orders/{wo.id}/edit').get_data(as_text=True)
    assert 'status_changed_at' not in form
