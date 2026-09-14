"""Two instances reached through one browser must not evict each other.

Cookies are not scoped by port (RFC 6265 leaves the port out of a cookie's
identity), so two instances on the same host at different ports share one jar.
With identical cookie names the second login overwrites the first, and the first
instance then receives a cookie signed with the other's SECRET_KEY, fails to
validate it, and shows you as signed out.
"""
import os
import tempfile

import pytest

from app import create_app
from app.extensions import db
from tests.conftest import CSRF, make_user


def build(secret, **extra):
    tmp = tempfile.TemporaryDirectory()
    application = create_app(config_overrides={
        'TESTING': True,
        'SECRET_KEY': secret,
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{os.path.join(tmp.name, "t.db")}',
        'UPLOAD_FOLDER': os.path.join(tmp.name, 'uploads'),
        'SCHEDULER_ENABLED': False,
        'WTF_CSRF_ENABLED': False,
        **extra,
    })
    application._tmp = tmp          # keep it alive for the test's lifetime
    with application.app_context():
        db.create_all()
        make_user('kevin', role='admin', password='Password123!')
    return application


def test_two_instances_use_different_session_cookie_names():
    a, b = build('secret-alpha'), build('secret-beta')
    assert a.config['SESSION_COOKIE_NAME'] != b.config['SESSION_COOKIE_NAME'], (
        'both instances name their session cookie the same thing, so one '
        'overwrites the other for anyone using both in one browser'
    )


def test_two_instances_use_different_remember_cookie_names():
    a, b = build('secret-alpha'), build('secret-beta')
    assert a.config['REMEMBER_COOKIE_NAME'] != b.config['REMEMBER_COOKIE_NAME']


def test_the_name_is_stable_for_one_instance():
    """It must not change between restarts, or every restart signs everybody
    out — the opposite of the bug being fixed."""
    assert (build('same-secret').config['SESSION_COOKIE_NAME']
            == build('same-secret').config['SESSION_COOKIE_NAME'])


def test_an_operator_can_set_the_suffix_explicitly(monkeypatch):
    """Two instances deliberately sharing a SECRET_KEY would otherwise still
    collide, since the suffix is derived from that key."""
    monkeypatch.setenv('COOKIE_SUFFIX', 'workshop')
    assert build('shared-secret').config['SESSION_COOKIE_NAME'] \
        == 'home_cmms_session_workshop'


def test_a_hostile_suffix_cannot_break_the_header(monkeypatch):
    """A cookie name is a token. Spaces or separators would either be dropped or
    let something be appended to the Set-Cookie header."""
    monkeypatch.setenv('COOKIE_SUFFIX', 'a b; Path=/; Domain=evil.test')
    name = build('shared-secret').config['SESSION_COOKIE_NAME']
    assert name == 'home_cmms_session_abPathDomainevil.test'.replace('.', '')


def test_an_explicit_name_still_wins():
    """Nothing here should override a deliberate choice."""
    app = build('any-secret', SESSION_COOKIE_NAME='chosen_by_hand')
    assert app.config['SESSION_COOKIE_NAME'] == 'chosen_by_hand'


def cookie_names(application):
    """The cookie names an instance actually puts on the wire when signing in.

    A real sign-in, with `remember` set, so both cookies are exercised. An
    earlier version of this posted bad credentials to avoid the CSRF plumbing —
    which returned 503 from the first-run gate, set no cookies at all, and made
    the comparison below pass against two empty sets.
    """
    client = application.test_client()
    with client.session_transaction() as session:
        session['csrf_token'] = CSRF
    response = client.post('/auth/login', data={
        'username': 'kevin', 'password': 'Password123!',
        'csrf_token': CSRF, 'remember': '1',
    })
    names = {header.split('=', 1)[0]
             for key, header in response.headers
             if key.lower() == 'set-cookie'}
    assert names, 'the sign-in set no cookies, so this compares nothing'
    return names


def test_the_names_on_the_wire_do_not_overlap():
    """The bug itself. Two instances at the same hostname on different ports
    share a cookie jar, so any name they have in common is one overwriting the
    other — and a cookie signed with the wrong SECRET_KEY reads as signed out.
    """
    shared = cookie_names(build('secret-alpha')) & cookie_names(build('secret-beta'))
    assert not shared, (
        f'both instances set these cookies by the same name: {sorted(shared)}. '
        f'In one browser, signing into either evicts the other.'
    )
