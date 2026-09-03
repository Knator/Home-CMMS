"""Sign-in and API-token rate limiting, and the key that is no longer shipped."""
import importlib
import os
import stat

import pytest

from app import security
from app.models.auth_attempt import AuthAttempt
from app.models.api_token import ApiToken
from tests.conftest import CSRF, make_user, prime_csrf


def try_login(client, username='tester', password='wrong'):
    prime_csrf(client)
    return client.post('/auth/login',
                       data={'username': username, 'password': password, 'csrf_token': CSRF})


def locked(response):
    return 'Too many failed' in response.get_data(as_text=True)


# ── login lockout ──────────────────────────────────────────────────────────

def test_a_few_typos_are_tolerated(client, db, user):
    for _ in range(security.MAX_IDENTIFIER_FAILURES - 1):
        assert not locked(try_login(client))


def test_repeated_failures_lock_the_account(client, db, user):
    for _ in range(security.MAX_IDENTIFIER_FAILURES):
        try_login(client)
    assert locked(try_login(client))


def test_the_lock_holds_even_with_the_right_password(client, db, user):
    """Otherwise the limit does nothing once the guess finally lands."""
    for _ in range(security.MAX_IDENTIFIER_FAILURES):
        try_login(client)
    response = try_login(client, password='password123')
    assert locked(response)
    assert response.status_code == 200      # not signed in


def test_a_successful_sign_in_clears_the_count(client, db, user):
    for _ in range(security.MAX_IDENTIFIER_FAILURES - 1):
        try_login(client)
    assert try_login(client, password='password123').status_code == 302

    # Back to a clean slate rather than one typo from a lockout.
    for _ in range(security.MAX_IDENTIFIER_FAILURES - 1):
        assert not locked(try_login(client))


def test_locking_one_account_does_not_lock_another(client, db, user):
    make_user('second')
    for _ in range(security.MAX_IDENTIFIER_FAILURES):
        try_login(client, username='tester')

    # Same source address, so only the per-address limit could bite, and it is
    # set higher than this.
    assert not locked(try_login(client, username='second'))


def test_spraying_many_accounts_still_trips_the_address_limit(client, db, user):
    """No single account accumulates enough failures, so the per-address limit
    is the one that has to catch this."""
    for i in range(security.MAX_IP_FAILURES + 1):
        response = try_login(client, username=f'nobody{i}')
    assert locked(response)


def test_attempts_are_recorded_without_the_password(client, db, user):
    try_login(client, password='hunter2-secret')
    attempt = AuthAttempt.query.one()
    assert attempt.identifier == 'tester'
    assert attempt.successful is False
    assert 'hunter2' not in repr(attempt)
    for column in AuthAttempt.__table__.columns:
        assert 'password' not in column.name


def test_a_blank_username_is_still_recorded(client, db, user):
    try_login(client, username='')
    assert AuthAttempt.query.one().identifier == '(blank)'


def test_the_message_does_not_reveal_whether_the_account_exists(client, db, user):
    real = try_login(client, username='tester').get_data(as_text=True)
    fake = try_login(client, username='nosuchuser').get_data(as_text=True)
    assert 'Invalid username or password' in real
    assert 'Invalid username or password' in fake


# ── API token limiting ─────────────────────────────────────────────────────

def test_token_guessing_is_throttled(client, db, user):
    for i in range(security.MAX_IP_FAILURES + 1):
        response = client.get('/api/v1/assets',
                              headers={'Authorization': f'Bearer guess-{i}'})
    assert response.status_code == 429
    assert response.headers.get('Retry-After')
    assert response.is_json


def test_a_valid_token_still_works_below_the_limit(client, db, user):
    from app.extensions import db as _db
    _, raw = ApiToken.issue(user, 'Integration')
    _db.session.commit()

    for i in range(3):
        client.get('/api/v1/assets', headers={'Authorization': f'Bearer bad-{i}'})
    assert client.get('/api/v1/assets',
                      headers={'Authorization': f'Bearer {raw}'}).status_code == 200


# ── persistence and the escape hatch ───────────────────────────────────────

def test_lockouts_survive_a_restart(client, db, user, app):
    """An in-memory counter would be cleared by crashing the app."""
    for _ in range(security.MAX_IDENTIFIER_FAILURES):
        try_login(client)

    fresh = app.test_client()      # a new client, as after a restart
    assert locked(try_login(fresh))


def test_an_admin_can_clear_lockouts(client, db, app, login):
    make_user('boss', role='admin')
    for _ in range(security.MAX_IDENTIFIER_FAILURES):
        try_login(client, username='someone')

    login('boss')
    client.post('/admin/maintenance/clear-lockouts', data={'csrf_token': CSRF})
    assert AuthAttempt.query.filter_by(successful=False).count() == 0
    assert security.lockout_remaining(identifier='someone') == 0


def test_clearing_lockouts_is_admin_only(client, db, user, login):
    login()
    response = client.post('/admin/maintenance/clear-lockouts', data={'csrf_token': CSRF})
    assert response.status_code == 302
    assert '/admin' not in response.headers['Location']


def test_old_attempts_are_pruned(client, db, user, app):
    from datetime import timedelta
    from app.utils import utcnow

    db.session.add(AuthAttempt(identifier='ancient', ip_address='1.2.3.4',
                               successful=False,
                               created_at=utcnow() - security.RETENTION - timedelta(days=1)))
    db.session.commit()

    try_login(client, password='password123')      # a success prunes
    assert AuthAttempt.query.filter_by(identifier='ancient').count() == 0


def test_failures_are_visible_to_an_admin(client, db, app, login):
    make_user('boss', role='admin')
    try_login(client, username='intruder')

    login('boss')
    body = client.get('/admin/maintenance').get_data(as_text=True)
    assert 'Sign-in Attempts' in body
    assert 'intruder' in body


# ── the signing key ────────────────────────────────────────────────────────

def test_no_default_key_is_shipped():
    """A constant in the source is identical on every install, and anyone
    holding it can forge a session for any account."""
    source = open('config.py').read()
    assert 'dev-secret-change-in-production' not in source


def test_a_key_is_generated_and_persisted(tmp_path, monkeypatch):
    import config as config_module

    monkeypatch.delenv('SECRET_KEY', raising=False)
    monkeypatch.setattr(config_module, 'INSTANCE_DIR', str(tmp_path), raising=False)
    monkeypatch.setattr(config_module, 'SECRET_KEY_FILE', str(tmp_path / 'secret_key'),
                        raising=False)

    first = config_module._secret_key()
    assert len(first) >= 64
    assert (tmp_path / 'secret_key').exists()
    # Stable, or every restart would sign everybody out.
    assert config_module._secret_key() == first


def test_the_key_file_is_not_world_readable(tmp_path, monkeypatch):
    import config as config_module

    monkeypatch.delenv('SECRET_KEY', raising=False)
    monkeypatch.setattr(config_module, 'INSTANCE_DIR', str(tmp_path), raising=False)
    monkeypatch.setattr(config_module, 'SECRET_KEY_FILE', str(tmp_path / 'secret_key'),
                        raising=False)
    config_module._secret_key()

    mode = stat.S_IMODE(os.stat(tmp_path / 'secret_key').st_mode)
    assert mode == 0o600


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch):
    import config as config_module

    monkeypatch.setenv('SECRET_KEY', 'from-the-environment')
    monkeypatch.setattr(config_module, 'SECRET_KEY_FILE', str(tmp_path / 'secret_key'),
                        raising=False)
    assert config_module._secret_key() == 'from-the-environment'


# ── the debugger ───────────────────────────────────────────────────────────

def test_debug_is_refused_on_a_network_interface():
    """The Werkzeug console runs arbitrary Python for anyone who can reach it."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, 'run.py'],
        env={**os.environ, 'FLASK_DEBUG': '1', 'HOST': '0.0.0.0', 'PORT': '5198',
             'SCHEDULER_ENABLED': '0'},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert 'Refusing to start' in (result.stderr + result.stdout)


# ── the log does not swamp the maintenance page ────────────────────────────

def _pile_up_failures(client, count, ip='198.51.100.7'):
    """Flood from a different address than the admin will sign in from.

    The per-address limit is cumulative, so flooding from the admin's own
    address would lock the admin out too — which is the intended behaviour, but
    not what these tests are about.
    """
    for i in range(count):
        prime_csrf(client)
        client.post('/auth/login',
                    data={'username': f'intruder{i}', 'password': 'wrong',
                          'csrf_token': CSRF},
                    environ_base={'REMOTE_ADDR': ip})


def test_the_card_shows_only_a_handful(client, db, app, login):
    make_user('boss', role='admin')
    _pile_up_failures(client, 40)

    login('boss')
    body = client.get('/admin/maintenance').get_data(as_text=True)
    shown = body.count('intruder')
    assert shown <= security.CARD_FAILURE_LIMIT


def test_the_card_says_how_many_more_there_are(client, db, app, login):
    make_user('boss', role='admin')
    _pile_up_failures(client, 40)

    login('boss')
    body = client.get('/admin/maintenance').get_data(as_text=True)
    assert 'most recent of' in body
    assert 'View all' in body


def test_backups_remain_reachable_under_a_flood(client, db, app, login):
    """The reason for the limit: a busy day of failures must not bury the
    controls underneath it."""
    make_user('boss', role='admin')
    _pile_up_failures(client, 200)

    login('boss')
    body = client.get('/admin/maintenance').get_data(as_text=True)
    assert 'Backups' in body
    assert 'Create backup' in body
    assert body.count('intruder') <= security.CARD_FAILURE_LIMIT


def test_the_full_log_has_its_own_page(client, db, app, login):
    make_user('boss', role='admin')
    _pile_up_failures(client, 3)

    login('boss')
    body = client.get('/admin/sign-in-attempts').get_data(as_text=True)
    for i in range(3):
        assert f'intruder{i}' in body


def test_the_log_is_paginated(client, db, app, login):
    """Seeded directly: the address limit stops recording long before a second
    page could be filled by real attempts."""
    from app.models.auth_attempt import AuthAttempt as Attempt

    make_user('boss', role='admin')
    for i in range(security.PAGE_SIZE + 10):
        db.session.add(Attempt(identifier=f'intruder{i}', ip_address='198.51.100.7',
                               successful=False))
    db.session.commit()

    login('boss')
    first = client.get('/admin/sign-in-attempts').get_data(as_text=True)
    assert 'Page 1 of 2' in first
    assert 'Older' in first

    second = client.get('/admin/sign-in-attempts?page=2').get_data(as_text=True)
    assert 'Page 2 of 2' in second
    assert 'Newer' in second


def test_the_log_can_show_successes_too(client, db, user, app, login):
    make_user('boss', role='admin')
    # A separate client, because signing in as tester here would leave this one
    # authenticated as a non-admin.
    other = app.test_client()
    prime_csrf(other)
    other.post('/auth/login', data={'username': 'tester', 'password': 'password123',
                                    'csrf_token': CSRF})

    login('boss')
    failed_only = client.get('/admin/sign-in-attempts').get_data(as_text=True)
    everything = client.get('/admin/sign-in-attempts?show=all').get_data(as_text=True)
    assert 'success' not in failed_only
    assert 'success' in everything


def test_a_junk_page_number_does_not_break_it(client, db, app, login):
    make_user('boss', role='admin')
    login('boss')
    for query in ('?page=abc', '?page=-5', '?page=99999', '?page=0'):
        assert client.get(f'/admin/sign-in-attempts{query}').status_code == 200, query


def test_the_log_is_admin_only(client, db, user, login):
    login()
    response = client.get('/admin/sign-in-attempts')
    assert response.status_code == 302
    assert '/admin' not in response.headers['Location']
