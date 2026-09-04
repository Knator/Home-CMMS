"""Regression tests for the auth, CSRF and access-control fixes."""
from tests.conftest import CSRF, prime_csrf


def test_login_rejects_absolute_next_url(client, user, login):
    """?next= must not be able to bounce a user off-site (open redirect)."""
    prime_csrf(client)
    response = client.post(
        '/auth/login?next=https://evil.example.com/phish',
        data={'username': 'tester', 'password': 'password123', 'csrf_token': CSRF},
    )
    assert response.status_code == 302
    assert 'evil.example.com' not in response.headers['Location']
    assert response.headers['Location'].endswith('/')


def test_login_rejects_protocol_relative_next_url(client, user):
    prime_csrf(client)
    response = client.post(
        '/auth/login?next=//evil.example.com/phish',
        data={'username': 'tester', 'password': 'password123', 'csrf_token': CSRF},
    )
    assert 'evil.example.com' not in response.headers['Location']


def test_login_honours_relative_next_url(client, user):
    prime_csrf(client)
    response = client.post(
        '/auth/login?next=/assets/',
        data={'username': 'tester', 'password': 'password123', 'csrf_token': CSRF},
    )
    assert response.headers['Location'] == '/assets/'


def test_post_without_csrf_token_is_forbidden(client, user, login):
    login()
    response = client.post('/locations/new', data={'name': 'No token'})
    assert response.status_code == 403


def test_deactivated_user_loses_active_session(client, db, user, login):
    """Deactivating an account must end sessions that are already signed in."""
    login()
    assert client.get('/').status_code == 200

    user.is_active = False
    db.session.commit()

    response = client.get('/')
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']


def test_deactivated_user_cannot_log_in(client, db, user, login):
    user.is_active = False
    db.session.commit()
    response = login()
    assert response.status_code == 200  # re-renders the form, no redirect


def test_anonymous_requests_redirect_to_login(client, user):
    for path in ('/', '/assets/', '/work-orders/', '/pms/', '/admin/users'):
        response = client.get(path)
        assert response.status_code == 302, path
        assert '/auth/login' in response.headers['Location'], path


def test_non_admin_cannot_reach_user_admin(client, user, login):
    login()
    response = client.get('/admin/users')
    assert response.status_code == 302
    assert '/admin' not in response.headers['Location']


def test_last_active_admin_cannot_be_demoted(client, admin, login):
    login('admin')
    response = client.post(f'/admin/users/{admin.id}/edit', data={
        'email': 'admin@example.com', 'role': 'user', 'csrf_token': CSRF,
    })
    assert response.status_code == 200
    assert admin.role == 'admin'


def test_admin_can_be_demoted_when_another_admin_exists(client, admin, db, login):
    from tests.conftest import make_user
    other = make_user('admin2', role='admin')
    login('admin')
    response = client.post(f'/admin/users/{other.id}/edit', data={
        'email': 'admin2@example.com', 'role': 'user', 'csrf_token': CSRF,
    })
    assert response.status_code == 302
    assert other.role == 'user'


# ── cookie hardening ───────────────────────────────────────────────────────

def _cookies(response):
    out = {}
    for header in response.headers.getlist('Set-Cookie'):
        name = header.split('=')[0]
        out[name] = {p.strip().split('=')[0].lower(): p.strip()
                     for p in header.split(';')[1:]}
    return out


def login_with_remember(client, remember):
    prime_csrf(client)
    data = {'username': 'tester', 'password': 'password123', 'csrf_token': CSRF}
    if remember:
        data['remember'] = 'y'
    return client.post('/auth/login', data=data)


def test_remember_me_issues_a_long_lived_cookie(client, user, login):
    cookies = _cookies(login_with_remember(client, True))
    assert 'expires' in cookies['remember_token']


def test_without_remember_me_there_is_no_login_token(client, user, login):
    cookies = _cookies(login_with_remember(client, False))
    # Flask-Login clears it rather than omitting it.
    assert cookies['remember_token']['max-age'] == 'Max-Age=0'


def test_the_remember_cookie_is_not_reachable_from_javascript(client, user, login):
    """It grants a login on its own, so script must not be able to read it."""
    cookies = _cookies(login_with_remember(client, True))
    assert 'httponly' in cookies['remember_token']


def test_the_remember_cookie_is_samesite_like_the_session(client, user, login):
    cookies = _cookies(login_with_remember(client, True))
    assert cookies['remember_token']['samesite'] == 'SameSite=Lax'


def test_the_session_cookie_now_expires(client, user, login):
    """PERMANENT_SESSION_LIFETIME is inert unless the session is marked
    permanent, which made the configured 8-hour timeout a no-op."""
    cookies = _cookies(login_with_remember(client, False))
    assert 'expires' in cookies['session']


def test_neither_cookie_is_secure_without_tls(app, client, user, login):
    """A Secure cookie is never sent over plain http, so forcing it on would
    break a LAN install that has no TLS."""
    assert app.config['SESSION_COOKIE_SECURE'] is False
    assert app.config['REMEMBER_COOKIE_SECURE'] is False

    cookies = _cookies(login_with_remember(client, True))
    assert 'secure' not in cookies['remember_token']


def test_both_cookies_are_secure_in_production(monkeypatch):
    """The remember cookie does not inherit SESSION_COOKIE_SECURE; it needs its
    own setting, and without one the more valuable token was the less protected."""
    import importlib
    import config as config_module

    monkeypatch.setenv('FLASK_ENV', 'production')
    monkeypatch.setenv('SECRET_KEY', 'x' * 64)
    importlib.reload(config_module)
    try:
        assert config_module.Config.SESSION_COOKIE_SECURE is True
        assert config_module.Config.REMEMBER_COOKIE_SECURE is True
    finally:
        monkeypatch.delenv('FLASK_ENV', raising=False)
        importlib.reload(config_module)


def test_the_remember_window_is_a_month_not_a_year(app):
    from datetime import timedelta
    assert app.config['REMEMBER_COOKIE_DURATION'] == timedelta(days=30)
