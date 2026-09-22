""""Remember me" has to outlive the eight-hour session, which it did not.

Restoring a user from the remember cookie builds a brand-new session holding
only `_user_id` and `_fresh=False`. It carries neither `_id` — the identifier
`session_protection='strong'` compares against — nor `permanent`. On the next
request the identifier could not match, and strong protection took its harshest
branch: empty the session *and* set `_remember='clear'`, which deletes the
remember cookie as well. The result was one working request after the session
lapsed, then a permanent logout, from a thirty-day credential.
"""
import os
import tempfile

import pytest

from app import create_app
from app.extensions import db
from tests.conftest import CSRF, make_user

FIREFOX = {'User-Agent': 'Mozilla/5.0 Firefox/1'}


@pytest.fixture
def instance():
    tmp = tempfile.TemporaryDirectory()
    application = create_app(config_overrides={
        'TESTING': True,
        'SECRET_KEY': 'remember-me-test',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{os.path.join(tmp.name, "t.db")}',
        'UPLOAD_FOLDER': os.path.join(tmp.name, 'uploads'),
        'SCHEDULER_ENABLED': False,
    })
    application._tmp = tmp
    with application.app_context():
        db.create_all()
        make_user('tester', role='admin', password='Password123!')
    return application


def sign_in(application, remember):
    client = application.test_client()
    with client.session_transaction() as session:
        session['csrf_token'] = CSRF
    data = {'username': 'tester', 'password': 'Password123!', 'csrf_token': CSRF}
    if remember:
        data['remember'] = 'y'
    client.post('/auth/login', data=data, headers=FIREFOX)
    return client


def lapse(application, client):
    """Eight idle hours: the browser stops sending the expired session cookie."""
    client.delete_cookie(application.config['SESSION_COOKIE_NAME'])


def held(client):
    return {c.key for c in client._cookies.values()}


def test_remember_me_survives_the_session_lapsing(instance):
    """The bug, in the shape that proves it: it used to read [200, 302, 302...]
    — one request, then logged out for good."""
    client = sign_in(instance, remember=True)
    lapse(instance, client)

    codes = [client.get('/', headers=FIREFOX).status_code for _ in range(6)]
    assert codes == [200] * 6, codes


def test_the_lapse_does_not_destroy_the_remember_cookie(instance):
    """Strong protection did not merely reject the request — it cleared the
    credential, which is why nothing recovered on a later visit."""
    remember = instance.config['REMEMBER_COOKIE_NAME']
    client = sign_in(instance, remember=True)
    lapse(instance, client)

    for _ in range(3):
        client.get('/', headers=FIREFOX)
    assert remember in held(client), 'the remember cookie was thrown away'


def test_a_restored_session_matches_a_fresh_sign_in(instance):
    """Both stamps matter. `_id` stops the identifier check failing; `permanent`
    gives the restored session the same eight-hour idle life a sign-in gets,
    rather than lasting only until the browser closes."""
    client = sign_in(instance, remember=True)
    lapse(instance, client)
    client.get('/', headers=FIREFOX)

    with client.session_transaction() as session:
        assert session.get('_id'), 'no session identifier was stamped'
        assert session.permanent, 'the restored session was not made permanent'


def test_without_remember_me_the_lapse_signs_you_out(instance):
    """The control. Nothing here should keep someone signed in who did not ask
    to be remembered."""
    client = sign_in(instance, remember=False)
    lapse(instance, client)

    response = client.get('/', headers=FIREFOX)
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']
