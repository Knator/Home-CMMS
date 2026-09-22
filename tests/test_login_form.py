"""What the sign-in form does after a failed attempt, and the reveal toggle.

Every test needs the `user` fixture: with no account on record the
first-run gate redirects /auth/login to /setup, so there is no form to
inspect at all.

Retyping a username you already typed is the small daily friction; the password
is the part that must never come back, and neither must any hint about which of
the two was wrong.
"""
import pytest

from tests.conftest import CSRF, make_user, prime_csrf


def attempt(client, username='tester', password='wrong'):
    prime_csrf(client)
    return client.post('/auth/login', data={
        'username': username, 'password': password, 'csrf_token': CSRF,
    })


def field(body, name):
    """The rendered <input> tag for a given name."""
    i = body.index(f'name="{name}"')
    return body[body.rindex('<input', 0, i):body.index('>', i) + 1]


def test_a_failed_sign_in_keeps_the_username(client, db, user):
    body = attempt(client, username='tester').get_data(as_text=True)
    assert 'value="tester"' in field(body, 'username')


def test_a_failed_sign_in_never_returns_the_password(client, db, user):
    body = attempt(client, password='hunter2-secret').get_data(as_text=True)
    assert 'hunter2-secret' not in body
    assert 'value=' not in field(body, 'password')


def test_an_unknown_username_comes_back_too(client, db, user):
    """Otherwise the form tells you the account does not exist by forgetting
    it, which is exactly the disclosure the flat error message avoids."""
    body = attempt(client, username='nobody').get_data(as_text=True)
    assert 'value="nobody"' in field(body, 'username')


def test_the_username_is_escaped_on_the_way_back(client, db, user):
    body = attempt(client, username='"><script>alert(1)</script>').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in body
    assert '&lt;script&gt;' in body or '&amp;lt;' in body


def test_focus_moves_to_the_password_after_a_failure(client, db, user):
    """A returning username means the password is the thing to fix."""
    body = attempt(client).get_data(as_text=True)
    assert 'autofocus' in field(body, 'password')
    assert 'autofocus' not in field(body, 'username')


def test_a_fresh_form_focuses_the_username(client, db, user):
    body = client.get('/auth/login').get_data(as_text=True)
    assert 'autofocus' in field(body, 'username')


def test_the_reveal_toggle_is_outside_the_password_input(client, db, user):
    """Asked for deliberately: a control layered inside the box covers the
    characters being typed."""
    body = client.get('/auth/login').get_data(as_text=True)
    row = body[body.index('class="password-row"'):body.index('</div>', body.index('class="password-row"'))]
    assert 'id="reveal-password"' in row
    assert row.index('name="password"') < row.index('id="reveal-password"')


def test_the_toggle_is_hidden_until_javascript_enables_it(client, db, user):
    """It can only work with script, and a dead control is worse than none."""
    body = client.get('/auth/login').get_data(as_text=True)
    button = body[body.index('id="reveal-password"'):]
    assert 'hidden' in button[:button.index('>')]
    css = (__import__('pathlib').Path('app/static/css/main.css')).read_text()
    assert '.reveal-toggle[hidden] { display: none; }' in css, (
        'the class rule sets display, which outranks the user agent [hidden] rule'
    )
