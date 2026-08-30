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


def test_anonymous_requests_redirect_to_login(client):
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
