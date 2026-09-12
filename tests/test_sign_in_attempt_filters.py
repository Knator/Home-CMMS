"""Filtering the sign-in attempt log.

Outcome, a local date range, and an IP search. The date range is the one with a
trap in it: created_at is stored UTC and the admin picks local dates.
"""
from datetime import date, timedelta

import pytest

from app.extensions import db as _db
from app.models.auth_attempt import AuthAttempt
from app.utils import local_day_start_utc
from tests.conftest import prime_csrf


@pytest.fixture
def admin_client(client, db, admin, login):
    login('admin')
    prime_csrf(client)
    return client


@pytest.fixture
def attempts(db):
    today = date.today()
    rows = [
        ('kevin', '192.168.1.10', True, today),
        ('kevin', '192.168.1.10', False, today),
        ('root', '203.0.113.9', False, today),
        ('kevin', '192.168.1.50', False, today - timedelta(days=3)),
        ('olduser', '10.0.0.5', True, today - timedelta(days=10)),
    ]
    for identifier, ip, ok, day in rows:
        _db.session.add(AuthAttempt(
            identifier=identifier, ip_address=ip, successful=ok,
            # midday local, so a timezone slip would move it across a boundary
            created_at=local_day_start_utc(day) + timedelta(hours=12)))
    _db.session.commit()
    return today


def rows(client, query=''):
    html = client.get('/admin/sign-in-attempts' + query).get_data(as_text=True)
    return html


def identifiers(client, query=''):
    html = rows(client, query)
    return {name for name in ('kevin', 'root', 'olduser') if name in html}


# ── outcome ────────────────────────────────────────────────────────────────

def test_failed_only_is_the_default(admin_client, attempts):
    html = rows(admin_client)
    assert 'olduser' not in html          # its only attempt succeeded


def test_successful_only(admin_client, attempts):
    html = rows(admin_client, '?outcome=successful')
    assert 'olduser' in html
    assert 'root' not in html             # its only attempt failed


def test_all_shows_both(admin_client, attempts):
    assert {'kevin', 'root', 'olduser'} <= identifiers(admin_client, '?outcome=all')


def test_an_unknown_outcome_falls_back_to_failed(admin_client, attempts):
    assert 'olduser' not in rows(admin_client, '?outcome=nonsense')


def test_the_result_column_always_shows(admin_client, attempts):
    """Worth seeing even when filtered to one outcome — it is the thing being
    filtered on."""
    assert '>Result<' in rows(admin_client)


# ── IP search ──────────────────────────────────────────────────────────────

def test_an_exact_address_matches(admin_client, attempts):
    assert identifiers(admin_client, '?outcome=all&ip=203.0.113.9') == {'root'}


def test_a_partial_address_matches_a_subnet(admin_client, attempts):
    """`192.168.` should find the subnet rather than needing the exact address."""
    assert identifiers(admin_client, '?outcome=all&ip=192.168.') == {'kevin'}


def test_an_ip_with_no_matches_returns_nothing(admin_client, attempts):
    assert identifiers(admin_client, '?outcome=all&ip=8.8.8.8') == set()


# ── date range, in local time ──────────────────────────────────────────────

def test_from_excludes_anything_earlier(admin_client, attempts):
    today = attempts
    assert identifiers(admin_client, f'?outcome=all&from={today}') == {'kevin', 'root'}


def test_to_is_inclusive_of_that_whole_day(admin_client, attempts):
    """A `to` date must cover the day it names, not stop at its midnight."""
    today = attempts
    three_days_ago = today - timedelta(days=3)
    found = identifiers(admin_client, f'?outcome=all&to={three_days_ago}')
    assert 'kevin' in found          # the attempt three days ago, at midday
    assert 'olduser' in found        # ten days ago
    assert 'root' not in found       # today


def test_a_range_covers_both_ends(admin_client, attempts):
    today = attempts
    found = identifiers(admin_client, f'?outcome=all&from={today - timedelta(days=5)}&to={today}')
    assert found == {'kevin', 'root'}
    assert 'olduser' not in found


def test_an_attempt_today_is_found_by_todays_date(admin_client, attempts):
    """The timezone trap: created_at is UTC, the filter is a local date. On a
    machine west of UTC a naive comparison drops today's later attempts."""
    today = attempts
    assert identifiers(admin_client, f'?outcome=all&from={today}&to={today}') == {
        'kevin', 'root'}


def test_a_malformed_date_is_ignored_rather_than_raising(admin_client, attempts):
    response = admin_client.get('/admin/sign-in-attempts?outcome=all&from=not-a-date')
    assert response.status_code == 200


# ── together ───────────────────────────────────────────────────────────────

def test_the_filters_narrow_each_other(admin_client, attempts):
    today = attempts
    found = identifiers(
        admin_client, f'?outcome=failed&ip=192.168.&from={today}&to={today}')
    assert found == {'kevin'}


def test_paging_carries_the_filters(admin_client, db, attempts):
    """Otherwise page two silently drops them and shows everything."""
    for i in range(60):
        _db.session.add(AuthAttempt(identifier=f'bulk{i}', ip_address='172.16.0.1',
                                    successful=False))
    _db.session.commit()

    html = rows(admin_client, '?outcome=failed&ip=172.16.')
    assert 'ip=172.16.' in html          # the Older link keeps the filter
    assert 'outcome=failed' in html


def test_clear_appears_only_when_something_is_filtered(admin_client, attempts):
    assert '>Clear<' not in rows(admin_client)
    assert '>Clear<' in rows(admin_client, '?outcome=all')
    assert '>Clear<' in rows(admin_client, '?ip=10.')


def test_the_page_is_admin_only(client, db, user, login):
    login()
    assert client.get('/admin/sign-in-attempts').status_code in (302, 403)
