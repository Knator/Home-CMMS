"""First-run setup: open only while the instance has no users."""
import re

import pytest

from app.extensions import db
from app.models.user import User
from tests.conftest import CSRF, make_user, prime_csrf


def csrf_from(page):
    return re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)


def complete_setup(client, username='kevin', password='a-good-password', **overrides):
    token = csrf_from(client.get('/setup'))
    data = {'username': username, 'email': f'{username}@example.com',
            'password': password, 'confirm_password': password, 'csrf_token': token}
    data.update(overrides)
    return client.post('/setup', data=data)


# ── while no users exist ───────────────────────────────────────────────────

def test_the_setup_page_is_offered(client, db):
    assert client.get('/setup').status_code == 200


def test_every_page_leads_to_setup(client, db):
    """An unconfigured instance should be obvious, not a login nobody can pass."""
    for path in ('/', '/assets/', '/work-orders/', '/auth/login', '/admin/users'):
        response = client.get(path)
        assert response.status_code == 302, path
        assert response.headers['Location'] == '/setup', path


def test_the_api_says_so_in_json(client, db):
    response = client.get('/api/v1/assets')
    assert response.status_code == 503
    assert 'not been set up' in response.get_json()['error']


def test_the_public_docs_still_work(client, db):
    """They describe the API and contain no data, so there is nothing to gate."""
    assert client.get('/api/v1/docs').status_code == 200
    assert client.get('/api/v1/openapi.json').status_code == 200


# ── creating the first administrator ───────────────────────────────────────

def test_it_creates_an_admin_and_signs_them_in(client, db):
    response = complete_setup(client)
    assert response.status_code == 302
    assert response.headers['Location'] == '/'

    user = User.query.one()
    assert user.role == 'admin'
    assert user.check_password('a-good-password')
    assert client.get('/').status_code == 200          # already signed in


def test_a_short_password_is_refused(client, db):
    response = complete_setup(client, password='short')
    assert response.status_code == 200
    assert User.query.count() == 0


def test_mismatched_passwords_are_refused(client, db):
    response = complete_setup(client, confirm_password='something-else')
    assert 'do not match' in response.get_data(as_text=True)
    assert User.query.count() == 0


def test_a_bad_email_is_refused(client, db):
    complete_setup(client, email='not-an-email')
    assert User.query.count() == 0


def test_setup_requires_csrf(client, db):
    client.get('/setup')
    response = client.post('/setup', data={'username': 'x', 'email': 'x@example.com',
                                           'password': 'a-good-password',
                                           'confirm_password': 'a-good-password'})
    assert response.status_code == 403
    assert User.query.count() == 0


# ── once an administrator exists ───────────────────────────────────────────

def test_setup_closes_permanently(client, db):
    complete_setup(client)
    response = client.get('/setup')
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']


def test_it_cannot_be_used_to_add_a_second_admin(client, db, user):
    """The whole security model is that one account closes the door."""
    before = User.query.count()
    response = client.post('/setup', data={'username': 'intruder', 'email': 'i@example.com',
                                           'password': 'a-good-password',
                                           'confirm_password': 'a-good-password',
                                           'csrf_token': CSRF})
    assert response.status_code == 302
    assert User.query.count() == before
    assert User.query.filter_by(username='intruder').first() is None


def test_a_race_between_two_visitors_makes_only_one_admin(client, app, db):
    """Both loaded the form while it was empty; the second must lose."""
    first, second = app.test_client(), app.test_client()
    token_one = csrf_from(first.get('/setup'))
    token_two = csrf_from(second.get('/setup'))

    first.post('/setup', data={'username': 'one', 'email': 'one@example.com',
                               'password': 'a-good-password',
                               'confirm_password': 'a-good-password',
                               'csrf_token': token_one})
    second.post('/setup', data={'username': 'two', 'email': 'two@example.com',
                                'password': 'a-good-password',
                                'confirm_password': 'a-good-password',
                                'csrf_token': token_two})

    assert User.query.count() == 1
    assert User.query.one().username == 'one'


def test_normal_pages_work_again_afterwards(client, db):
    complete_setup(client)
    assert client.get('/assets/').status_code == 200
    assert client.get('/admin/users').status_code == 200      # the first user is an admin


# ── the optional bounded window ────────────────────────────────────────────

def test_the_window_can_be_bounded(client, app, db):
    """Portainer's trick: an instance left running unattended stops being
    claimable until someone restarts it."""
    from datetime import timedelta
    from app.utils import utcnow

    app.config['SETUP_WINDOW_MINUTES'] = 5
    app.config['STARTED_AT'] = utcnow() - timedelta(minutes=10)

    response = client.get('/setup')
    assert response.status_code == 403
    assert 'Setup window closed' in response.get_data(as_text=True)

    assert client.post('/setup', data={'username': 'late', 'email': 'l@example.com',
                                       'password': 'a-good-password',
                                       'confirm_password': 'a-good-password',
                                       'csrf_token': CSRF}).status_code == 403
    assert User.query.count() == 0


def test_the_window_is_unbounded_by_default(client, app, db):
    assert not app.config.get('SETUP_WINDOW_MINUTES')
    assert client.get('/setup').status_code == 200


def test_seeding_an_admin_up_front_closes_setup(client, db):
    """ADMIN_* in the container creates the account before anything listens,
    which is the way to avoid the window entirely."""
    make_user('seeded', role='admin')
    response = client.get('/setup')
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']


def test_an_empty_instance_does_not_show_a_login_page(client, db):
    """Before setup there is no account to sign in with, so offering a login
    form would be a dead end."""
    response = client.get('/auth/login')
    assert response.headers['Location'] == '/setup'


def test_the_login_page_returns_once_an_account_exists(client, db, user):
    assert client.get('/auth/login').status_code == 200
