"""Signing out has to actually sign you out.

`logout_user()` does not delete the remember-me cookie itself — it leaves a
marker in the session for Flask-Login's response hook to act on. Clearing the
session afterwards erased the marker, so the cookie survived: a thirty-day
login token left alive in the browser of someone who had just asked to leave.
"""
import pytest

from tests.conftest import CSRF, make_user, prime_csrf


@pytest.fixture
def signed_in(client, db):
    make_user('tester', role='user', password='Password123!')
    prime_csrf(client)
    client.post('/auth/login', data={
        'username': 'tester', 'password': 'Password123!',
        'csrf_token': CSRF, 'remember': 'y',
    })
    return client


def cookie_names(client):
    return {c.key for c in client._cookies.values()} if hasattr(client, '_cookies') \
        else {c.name for c in client.cookie_jar}


def test_signing_in_with_remember_me_works(signed_in):
    assert signed_in.get('/').status_code == 200


def test_logging_out_ends_the_session(app, signed_in):
    signed_in.get('/auth/logout')
    response = signed_in.get('/')
    assert response.status_code == 302, (
        'still signed in after logging out — the remember-me cookie is '
        're-authenticating, so Logout does not log you out'
    )
    assert '/auth/login' in response.headers['Location']


def test_the_remember_cookie_is_actually_deleted(app, signed_in):
    """The cookie itself, not just its effect: one left in the browser is a
    standing credential on a shared machine."""
    remember = app.config['REMEMBER_COOKIE_NAME']
    signed_in.get('/auth/logout')
    value = signed_in.get_cookie(remember)
    assert value is None or value.value == '', (
        f'{remember} survived logout with value {value!r}'
    )
