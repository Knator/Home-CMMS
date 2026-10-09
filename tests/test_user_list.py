"""The user list: search, role and status filters, a last-login range, paging,
and the filters surviving a trip through edit, create and Activate/Deactivate.
"""
import html
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from app.extensions import db as _db
from app.models.user import User
from app.navigation import PAGE_SIZE
from app.utils import local_day_start_utc
from tests.conftest import CSRF, make_user, prime_csrf


@pytest.fixture
def admin_client(client, db, login):
    make_user('boss', role='admin', password='Password123!')
    login('boss', 'Password123!')
    prime_csrf(client)
    return client


def person(username, role='user', display=None, email=None, active=True, last_login=None):
    u = make_user(username, role=role, password='Password123!', active=active)
    u.display_name = display
    if email:
        u.email = email
    u.last_login = last_login
    _db.session.commit()
    return u


def shown(client, url):
    """Usernames on the page, in order (the username line, or the label)."""
    body = client.get(url).get_data(as_text=True)
    rows = re.findall(r'<td>\s*([^<\n]+?)\s*(?:<div class="field-hint">([^<]+)</div>)?\s*</td>\s*<td class="col-hide-sm">', body)
    return [hint or name for name, hint in rows]


def count(client, url):
    body = client.get(url).get_data(as_text=True)
    pos = re.search(r'class="pager-position">(.*?)</span>', body, re.S)
    return int(re.search(r'(\d+) users?', html.unescape(pos.group(1))).group(1))


# ── search ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('needle,expected', [
    ('jordan', {'jordan'}),               # username
    ('Fixit', {'jordan'}),                # display name, any case
    ('lawns.example', {'avery'}),         # email
])
def test_search_looks_at_both_names_and_the_email(admin_client, needle, expected):
    person('jordan', display='Jordan Fixit', email='jordan@home.example')
    person('avery', email='avery@lawns.example')
    assert set(shown(admin_client, f'/admin/users?q={needle}')) == expected


def test_a_regex_search_works_like_the_other_lists(admin_client):
    person('jordan'); person('avery')
    assert shown(admin_client, '/admin/users?q=^(jor|ave)&regex=1') == ['avery', 'jordan']


# ── role and status ────────────────────────────────────────────────────────

def test_role_filter(admin_client):
    person('helper', role='user')
    assert set(shown(admin_client, '/admin/users?role=admin')) == {'boss'}
    assert set(shown(admin_client, '/admin/users?role=user')) == {'helper'}


def test_status_filter(admin_client):
    person('gone', active=False)
    assert shown(admin_client, '/admin/users?status=inactive') == ['gone']
    assert 'gone' not in shown(admin_client, '/admin/users?status=active')


def test_values_outside_the_vocabulary_filter_nothing(admin_client):
    person('helper')
    everyone = count(admin_client, '/admin/users')
    for junk in ('role=superuser', 'status=banned', 'from=notadate', 'to=2026-13-45'):
        assert count(admin_client, f'/admin/users?{junk}') == everyone, junk


# ── last login range ───────────────────────────────────────────────────────

@pytest.fixture
def off_utc(monkeypatch):
    """Pin a zone that is not UTC. On a UTC machine local midnight *is* UTC
    midnight, so comparing local dates straight against the UTC column would
    pass this test by accident — the same pinning test_local_time.py uses."""
    monkeypatch.setenv('TZ', 'America/New_York')
    time.tzset()
    yield
    monkeypatch.delenv('TZ', raising=False)
    time.tzset()


def test_the_range_is_local_days_and_to_is_inclusive(admin_client, off_utc):
    """Built from local_day_start_utc() at each edge, as the sign-in log is.
    A login one second before the next local day still counts; one second
    after it does not. Comparing local dates to the UTC column directly would
    get this wrong by the UTC offset."""
    day = date(2026, 6, 10)
    start = local_day_start_utc(day)
    next_start = local_day_start_utc(day + timedelta(days=1))
    person('early', last_login=start)
    person('late', last_login=next_start - timedelta(seconds=1))
    person('tomorrow', last_login=next_start)
    person('yesterday', last_login=start - timedelta(seconds=1))

    got = set(shown(admin_client, f'/admin/users?from={day}&to={day}'))
    assert got == {'early', 'late'}, got


def test_one_sided_ranges(admin_client):
    person('old', last_login=local_day_start_utc(date(2026, 1, 5)))
    person('new', last_login=local_day_start_utc(date(2026, 9, 5)))
    assert 'new' in shown(admin_client, '/admin/users?from=2026-06-01')
    assert 'old' not in shown(admin_client, '/admin/users?from=2026-06-01')
    assert 'old' in shown(admin_client, '/admin/users?to=2026-06-01')
    assert 'new' not in shown(admin_client, '/admin/users?to=2026-06-01')


def test_never_having_signed_in_is_outside_any_range(admin_client):
    """There is no login to fall inside the range — that is the answer to
    "who signed in between these dates"."""
    person('never', last_login=None)
    assert 'never' not in shown(admin_client, '/admin/users?from=2000-01-01')
    assert 'never' in shown(admin_client, '/admin/users')


def test_the_page_uses_the_browsers_date_picker(admin_client):
    body = admin_client.get('/admin/users?from=2026-01-01').get_data(as_text=True)
    assert re.search(r'<input type="date" id="from" name="from" value="2026-01-01">', body)
    assert re.search(r'<input type="date" id="to" name="to"', body)


# ── paging ─────────────────────────────────────────────────────────────────

def test_a_long_list_pages_with_the_shared_pager(admin_client):
    for i in range(PAGE_SIZE + 5):
        person(f'user{i:03d}')
    body = admin_client.get('/admin/users').get_data(as_text=True)
    assert f'Page 1 of 2' in body
    assert body.count('class="pager-position"') == 2, 'the top and bottom pagers'
    bar = body[body.index('<form method="get" class="filters">'):]
    bar = bar[:bar.index('</form>')]
    assert 'list-count' in bar and 'wo-step' in bar, 'no pager in the filter bar'


def test_the_count_shows_on_a_single_page(admin_client):
    person('helper')
    body = admin_client.get('/admin/users').get_data(as_text=True)
    assert '2 users' in body
    assert 'Page 1 of' not in body


# ── the filters survive the trip ───────────────────────────────────────────

def carries(url):
    return parse_qs(urlsplit(html.unescape(url)).query).get('role') == ['user']


def test_edit_and_activate_carry_the_filters(admin_client):
    person('helper')
    body = admin_client.get('/admin/users?role=user').get_data(as_text=True)
    edit = re.search(r'href="([^"]*/admin/users/\d+/edit[^"]*)"', body).group(1)
    toggle = re.search(r'action="([^"]*/toggle[^"]*)"', body).group(1)
    new = re.search(r'href="([^"]*/admin/users/new[^"]*)"', body).group(1)
    assert carries(edit) and carries(toggle) and carries(new), (edit, toggle, new)


def test_deactivating_returns_to_the_filtered_list(admin_client):
    helper = person('helper')
    response = admin_client.post(f'/admin/users/{helper.id}/toggle?role=user',
                                 data={'csrf_token': CSRF})
    assert carries(response.headers['Location']), response.headers['Location']


def test_a_refused_deactivation_also_keeps_them(admin_client):
    """Deactivating yourself is refused — that path redirects back too."""
    me = User.query.filter_by(username='boss').first()
    response = admin_client.post(f'/admin/users/{me.id}/toggle?role=user',
                                 data={'csrf_token': CSRF})
    assert carries(response.headers['Location']), response.headers['Location']


def test_saving_an_edit_returns_to_the_filtered_list(admin_client):
    helper = person('helper', email='helper@home.example')
    response = admin_client.post(f'/admin/users/{helper.id}/edit?role=user', data={
        'csrf_token': CSRF, 'email': 'helper@home.example', 'role': 'user',
        'display_name': '', 'new_password': ''})
    assert response.status_code == 302
    assert carries(response.headers['Location']), response.headers['Location']


def test_cancel_on_the_user_form_keeps_them(admin_client):
    body = admin_client.get('/admin/users/new?role=user').get_data(as_text=True)
    cancel = re.search(r'<a href="([^"]+)" class="btn btn-secondary">Cancel</a>', body).group(1)
    assert carries(cancel), cancel


def test_clear_really_clears(admin_client):
    body = admin_client.get('/admin/users?role=user&q=x').get_data(as_text=True)
    clear = re.search(r'<a href="([^"]+)" class="btn btn-ghost btn-sm">Clear</a>', body).group(1)
    assert '?' not in clear, clear


def test_a_search_with_no_match_says_so(admin_client):
    body = admin_client.get('/admin/users?q=zzzznobody').get_data(as_text=True)
    assert 'No users match the current filters.' in body
