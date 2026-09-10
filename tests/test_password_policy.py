"""One password policy, applied wherever a password is set.

Five things set a password — first-run setup, the admin create and edit forms,
a user changing their own, and create_admin.py. Each used to carry its own
length check; these pin that they all now go through the same rule, and that
the requirements shown on screen come from the same place that enforces them.
"""
import pytest

from app.extensions import db as _db
from app.models.user import User
from app.passwords import MIN_LENGTH, REQUIREMENTS, password_problems
from tests.conftest import CSRF, make_user, prime_csrf

VALID = 'Correct1Horse!'
INVALID = {
    'short': 'Ab1!xy',
    'no capital': 'password123!x',
    'no number': 'Passwordabc!',
    'no symbol': 'Password1234',
}


@pytest.fixture
def admin_client(client, db, login):
    make_user('admin', role='admin', password=VALID)
    login('admin', VALID)
    prime_csrf(client)
    return client


# ── the rule itself ────────────────────────────────────────────────────────

def test_a_password_meeting_every_rule_passes():
    assert password_problems(VALID) == []


@pytest.mark.parametrize('label,password', INVALID.items(), ids=list(INVALID))
def test_each_requirement_is_enforced(label, password):
    assert password_problems(password), f'{label!r} should have been rejected'


def test_every_failure_is_reported_at_once():
    """Not one per attempt: learning a four-part rule one round trip at a time
    is what makes people pick the first thing that scrapes through."""
    problems = password_problems('abc')
    assert len(problems) == len(REQUIREMENTS)


def test_the_minimum_is_twelve():
    assert MIN_LENGTH == 12
    eleven = 'Aa1!' + 'a' * 7
    twelve = 'Aa1!' + 'a' * 8
    assert len(eleven) == 11 and len(twelve) == 12
    assert password_problems(eleven) == ['At least 12 characters']
    assert password_problems(twelve) == []


def test_a_space_counts_as_a_symbol():
    """Passphrases should not be penalised for using the obvious separator."""
    assert password_problems('Correct Horse1 Battery') == []


# ── enforced on every path ─────────────────────────────────────────────────

@pytest.mark.parametrize('label,password', INVALID.items(), ids=list(INVALID))
def test_first_run_setup_refuses_a_weak_password(client, db, label, password):
    prime_csrf(client)
    client.post('/setup', data={
        'csrf_token': CSRF, 'username': 'root', 'email': 'r@example.com',
        'password': password, 'confirm_password': password,
    })
    assert User.query.count() == 0


def test_first_run_setup_accepts_a_good_one(client, db):
    prime_csrf(client)
    client.post('/setup', data={
        'csrf_token': CSRF, 'username': 'root', 'email': 'r@example.com',
        'password': VALID, 'confirm_password': VALID,
    })
    assert User.query.count() == 1


@pytest.mark.parametrize('label,password', INVALID.items(), ids=list(INVALID))
def test_admin_cannot_create_a_user_with_a_weak_password(admin_client, label, password):
    admin_client.post('/admin/users/new', data={
        'csrf_token': CSRF, 'username': 'new', 'email': 'n@example.com',
        'password': password, 'role': 'user',
    })
    assert User.query.filter_by(username='new').first() is None


@pytest.mark.parametrize('label,password', INVALID.items(), ids=list(INVALID))
def test_admin_cannot_reset_to_a_weak_password(admin_client, db, label, password):
    target = make_user('tech', password=VALID)
    admin_client.post(f'/admin/users/{target.id}/edit', data={
        'csrf_token': CSRF, 'email': target.email, 'role': 'user',
        'new_password': password,
    })
    assert _db.session.get(User, target.id).check_password(VALID)   # unchanged


def test_a_blank_password_on_edit_still_means_leave_it_alone(admin_client, db):
    target = make_user('tech', password=VALID)
    admin_client.post(f'/admin/users/{target.id}/edit', data={
        'csrf_token': CSRF, 'email': target.email, 'role': 'user',
        'new_password': '',
    })
    assert _db.session.get(User, target.id).check_password(VALID)


@pytest.mark.parametrize('label,password', INVALID.items(), ids=list(INVALID))
def test_users_cannot_change_to_a_weak_password(admin_client, label, password):
    admin_client.post('/auth/change-password', data={
        'csrf_token': CSRF, 'current_password': VALID,
        'new_password': password, 'confirm_password': password,
    })
    assert User.query.filter_by(username='admin').one().check_password(VALID)


def test_changing_to_a_good_password_works(admin_client):
    response = admin_client.post('/auth/change-password', data={
        'csrf_token': CSRF, 'current_password': VALID,
        'new_password': 'Another1Password!', 'confirm_password': 'Another1Password!',
    }, follow_redirects=True)
    assert b'Password updated successfully' in response.data


def test_the_cli_uses_the_same_rule(app, db):
    """An unattended ADMIN_PASSWORD must not create an account the app itself
    would refuse."""
    import create_admin
    assert any('password' in e.lower()
               for e in create_admin.validate('newadmin', 'a@example.com', 'weak'))
    assert not create_admin.validate('newadmin', 'a@example.com', VALID)


# ── the requirements are shown, from the same source ───────────────────────

@pytest.mark.parametrize('path', ['/auth/change-password', '/admin/users/new'])
def test_forms_show_the_requirements(admin_client, path):
    html = admin_client.get(path).get_data(as_text=True)
    assert 'req-hint' in html                      # the hoverable ?
    for requirement in REQUIREMENTS:
        assert requirement in html, requirement


def test_the_setup_page_shows_them_before_any_account_exists(client, db):
    html = client.get('/setup').get_data(as_text=True)
    assert 'req-hint' in html
    assert REQUIREMENTS[0] in html


@pytest.mark.parametrize('path', ['/auth/change-password', '/admin/users/new'])
def test_the_browser_minimum_matches_the_server(admin_client, path):
    """A field that accepts what the server rejects is just a wasted round trip."""
    html = admin_client.get(path).get_data(as_text=True)
    assert f'minlength="{MIN_LENGTH}"' in html


def test_the_hint_is_reachable_without_a_mouse(admin_client):
    """A title attribute would never appear for keyboard or touch users."""
    html = admin_client.get('/auth/change-password').get_data(as_text=True)
    assert 'tabindex="0"' in html
    assert 'aria-label="Password requirements' in html


# ── existing accounts are not locked out ───────────────────────────────────

def test_an_old_weak_password_still_signs_in(client, db, login):
    """The policy applies when a password is set. Enforcing it at sign-in would
    lock people out of an instance that predates it."""
    make_user('legacy', password='oldpass1')
    response = login('legacy', 'oldpass1')
    assert response.status_code == 302
