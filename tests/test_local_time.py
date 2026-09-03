"""Timestamps are stored in UTC and displayed in the host's timezone."""
import os
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from app.utils import format_datetime, local_timezone_name, to_local, utcnow


@pytest.fixture
def in_new_york(monkeypatch):
    """Pretend the host is in a timezone well away from UTC."""
    monkeypatch.setenv('TZ', 'America/New_York')
    time.tzset()
    yield
    monkeypatch.delenv('TZ', raising=False)
    time.tzset()


# ── conversion ─────────────────────────────────────────────────────────────

def test_a_stored_timestamp_shows_in_local_time(in_new_york):
    """The bug this fixes: a work order made at 20:09 on the 31st was stored as
    00:09 UTC on the 1st and displayed as the 1st."""
    stored = datetime(2026, 9, 1, 0, 9, 16)
    assert format_datetime(stored) == '2026-08-31 20:09'


def test_summer_and_winter_offsets_both_work(in_new_york):
    """A fixed offset would be wrong for half the year."""
    assert format_datetime(datetime(2026, 7, 1, 16, 0)) == '2026-07-01 12:00'   # EDT, -4
    assert format_datetime(datetime(2026, 1, 1, 16, 0)) == '2026-01-01 11:00'   # EST, -5


def test_naive_values_are_treated_as_utc(in_new_york):
    naive = datetime(2026, 6, 1, 12, 0)
    aware = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    assert format_datetime(naive) == format_datetime(aware)


def test_aware_values_are_converted_not_mangled(in_new_york):
    """APScheduler hands back timezone-aware values."""
    aware = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    assert to_local(aware).hour == 8


def test_missing_values_render_a_placeholder():
    assert format_datetime(None) == '—'
    assert format_datetime(None, empty='Never') == 'Never'


def test_the_format_can_be_chosen(in_new_york):
    stored = datetime(2026, 9, 1, 0, 9, 16)
    assert format_datetime(stored, '%Y-%m-%d') == '2026-08-31'
    assert format_datetime(stored, '%H:%M:%S') == '20:09:16'


def test_the_timezone_name_is_reportable(in_new_york):
    assert 'UTC-' in local_timezone_name()


def test_no_timezone_database_download_is_needed(in_new_york):
    """It reads the operating system's timezone, so it works with no network."""
    import sys
    assert 'tzdata' not in sys.modules
    assert format_datetime(datetime(2026, 9, 1, 0, 9))


# ── storage is unchanged ───────────────────────────────────────────────────

def test_storage_stays_utc(in_new_york):
    """Storing local time would be ambiguous across a daylight-saving fallback
    and would silently reinterpret old rows if the host's timezone changed."""
    stored = utcnow()
    assert abs((stored - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()) < 5


def test_a_round_trip_survives_a_timezone_change(monkeypatch):
    """The same instant reads correctly wherever the host is moved to."""
    stored = datetime(2026, 9, 1, 0, 9, 16)

    monkeypatch.setenv('TZ', 'America/New_York'); time.tzset()
    in_ny = format_datetime(stored)
    monkeypatch.setenv('TZ', 'Europe/London'); time.tzset()
    in_london = format_datetime(stored)
    monkeypatch.delenv('TZ', raising=False); time.tzset()

    assert in_ny == '2026-08-31 20:09'
    assert in_london == '2026-09-01 01:09'


# ── rendered pages ─────────────────────────────────────────────────────────

def test_pages_show_local_times(client, db, user, login, in_new_york):
    from app.services import create_work_order
    from app.extensions import db as _db

    wo = create_work_order(title='Evening job')
    wo.created_at = datetime(2026, 9, 1, 0, 9, 16)      # 20:09 the previous day, locally
    _db.session.commit()

    login()
    body = client.get(f'/work-orders/{wo.id}').get_data(as_text=True)
    assert '2026-08-31 20:09' in body
    assert '2026-09-01 00:09' not in body


def test_calendar_dates_are_left_alone(client, db, user, login, in_new_york):
    """due_date and friends are already local dates, not instants — shifting
    them would move a due date by a day."""
    from app.services import create_work_order

    wo = create_work_order(title='Due job', due_date=date(2026, 9, 1),
                           status='completed', completed_date=date(2026, 9, 1))
    login()
    body = client.get(f'/work-orders/{wo.id}').get_data(as_text=True)
    assert body.count('2026-09-01') >= 2


def test_the_maintenance_page_reports_the_timezone(client, db, app, login, in_new_york):
    from tests.conftest import make_user

    make_user('boss', role='admin')
    login('boss')
    body = client.get('/admin/maintenance').get_data(as_text=True)
    assert 'Host timezone' in body
    assert 'UTC-' in body
