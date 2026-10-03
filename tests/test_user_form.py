"""A rejected user form keeps what was typed, except the password.

Every error re-renders the form rather than redirecting, and it used to start
again from scratch: blank when creating, the stored record when editing. One
weak password threw away the username, email, display name and role — and on
the edit form silently reverted changes the admin had just made.
"""
import pathlib
import re
from html.parser import HTMLParser

import pytest

from app.models.user import User
from tests.conftest import CSRF, make_user, prime_csrf

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEAK = 'short'


@pytest.fixture
def admin_client(client, db, login):
    make_user('boss', role='admin', password='Password123!')
    login('boss', 'Password123!')
    prime_csrf(client)
    return client


class Fields(HTMLParser):
    """Values of every input and the selected option of every select."""

    def __init__(self):
        super().__init__()
        self.values, self._select = {}, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'input' and a.get('name'):
            self.values[a['name']] = a.get('value')
        elif tag == 'select':
            self._select = a.get('name')
        elif tag == 'option' and self._select and 'selected' in a:
            self.values[self._select] = a.get('value')

    def handle_endtag(self, tag):
        if tag == 'select':
            self._select = None


def fields(body):
    parser = Fields()
    parser.feed(body)
    return parser.values


def create(client, **overrides):
    data = {'csrf_token': CSRF, 'username': 'newperson', 'email': 'new@example.com',
            'display_name': 'New Person', 'role': 'admin', 'password': WEAK}
    data.update(overrides)
    return client.post('/admin/users/new', data=data)


# ── creating ───────────────────────────────────────────────────────────────

def test_a_weak_password_keeps_everything_else(admin_client):
    """The reported bug."""
    body = create(admin_client).get_data(as_text=True)
    values = fields(body)

    assert values['username'] == 'newperson'
    assert values['email'] == 'new@example.com'
    assert values['display_name'] == 'New Person'
    assert values['role'] == 'admin'


def test_the_password_is_cleared_and_never_written_back(admin_client):
    body = create(admin_client, password='Weakish1').get_data(as_text=True)
    assert fields(body).get('password') in (None, '')
    assert 'Weakish1' not in body, 'the rejected password was echoed into the page'


def test_the_problem_is_reported(admin_client):
    body = create(admin_client).get_data(as_text=True)
    assert 'alert-error' in body
    assert User.query.filter_by(username='newperson').first() is None


def test_other_errors_keep_the_form_too(admin_client):
    """Not only the password: a taken username used to wipe the form as well."""
    body = create(admin_client, username='boss',
                  password='GoodPassword123!').get_data(as_text=True)
    values = fields(body)
    assert values['username'] == 'boss'
    assert values['email'] == 'new@example.com'
    assert 'Username already taken' in body


def test_a_fresh_form_is_still_blank(admin_client):
    values = fields(admin_client.get('/admin/users/new').get_data(as_text=True))
    assert values['username'] in (None, '')
    assert values['email'] in (None, '')
    assert values['role'] == 'user'


def test_a_good_submission_still_creates(admin_client):
    response = create(admin_client, password='GoodPassword123!')
    assert response.status_code == 302
    assert User.query.filter_by(username='newperson').first() is not None


# ── editing ────────────────────────────────────────────────────────────────

def test_editing_keeps_the_changes_rather_than_reverting_them(admin_client):
    """The quieter half: the edit form re-rendered the stored record, so the
    admin's changes vanished and the page looked as though nothing had been
    typed at all."""
    person = make_user('jo', role='user', password='Password123!')
    body = admin_client.post(f'/admin/users/{person.id}/edit', data={
        'csrf_token': CSRF, 'email': 'changed@example.com',
        'display_name': 'Jo Changed', 'role': 'admin', 'new_password': WEAK,
    }).get_data(as_text=True)

    values = fields(body)
    assert values['email'] == 'changed@example.com'
    assert values['display_name'] == 'Jo Changed'
    assert values['role'] == 'admin'
    assert fields(body).get('new_password') in (None, '')


def test_an_edit_form_still_opens_on_the_stored_values(admin_client):
    person = make_user('kit', role='user', password='Password123!')
    values = fields(admin_client.get(f'/admin/users/{person.id}/edit').get_data(as_text=True))
    assert values['email'] == person.email
    assert values['role'] == 'user'


def test_a_rejected_edit_changes_nothing(admin_client):
    person = make_user('robin', role='user', password='Password123!')
    admin_client.post(f'/admin/users/{person.id}/edit', data={
        'csrf_token': CSRF, 'email': 'changed@example.com',
        'display_name': '', 'role': 'admin', 'new_password': WEAK,
    })
    from app.extensions import db
    db.session.refresh(person)
    assert person.email != 'changed@example.com'
    assert person.role == 'user'


# ── the show-password button ───────────────────────────────────────────────

def reveal_markup(body):
    found = re.search(r'<button type="button" class="reveal-toggle".*?</button>', body, re.S)
    return re.sub(r'\s+', ' ', found.group(0)) if found else None


def test_the_user_form_has_a_reveal_button(admin_client):
    body = admin_client.get('/admin/users/new').get_data(as_text=True)
    button = reveal_markup(body)
    assert button, 'no reveal button on the new user form'
    assert 'aria-controls="password"' in button
    assert 'id="password"' in body


def test_it_matches_the_sign_in_button(admin_client, client):
    """Asked for in so many words, and guaranteed by both coming from one macro
    rather than by two copies happening to agree."""
    form = reveal_markup(admin_client.get('/admin/users/new').get_data(as_text=True))
    client.get('/auth/logout')
    login = reveal_markup(client.get('/auth/login').get_data(as_text=True))
    assert form == login


def test_one_script_drives_every_toggle():
    """The sign-in page used to carry its own inline copy."""
    login = (ROOT / 'app/templates/auth/login.html').read_text()
    base = (ROOT / 'app/templates/base.html').read_text()
    assert '<script>' not in login, 'an inline script crept back into the sign-in page'
    assert 'password-reveal.js' in login
    assert 'password-reveal.js' in base


def test_the_toggle_is_wired_by_the_control_it_names():
    """aria-controls is how the script finds its field, so any page can have
    several — the edit form and the setup screen included."""
    js = (ROOT / 'app/static/js/password-reveal.js').read_text()
    assert "button.reveal-toggle[aria-controls]" in js
    assert "getAttribute('aria-controls')" in js
