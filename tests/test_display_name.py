"""A display name, separate from the username people sign in with.

The username stays the identity: it is unique, it is what authenticates, and it
is what the API addresses users by. The display name is only ever what appears
on screen, so it is nullable (meaning "not set") and deliberately not unique.
"""
import pytest

from app.extensions import db as _db
from app.models.user import User
from app.services import create_work_order
from tests.conftest import CSRF, make_user, prime_csrf


@pytest.fixture
def admin_client(client, db, admin, login):
    login('admin')
    prime_csrf(client)
    return client


# ── the model ──────────────────────────────────────────────────────────────

def test_label_falls_back_to_the_username(db):
    user = make_user('kcoleman')
    assert user.display_name is None
    assert user.label == 'kcoleman'


def test_label_prefers_the_display_name(db):
    user = make_user('kcoleman')
    user.display_name = 'Kevin'
    _db.session.commit()
    assert user.label == 'Kevin'


def test_the_default_follows_a_renamed_username(db):
    """Why the column is NULL rather than seeded with the username: a copy
    taken at creation would go stale the moment the username changed."""
    user = make_user('kcoleman')
    user.username = 'kevin'
    _db.session.commit()
    assert user.label == 'kevin'


def test_display_names_need_not_be_unique(db):
    """Two people called Alex is a real situation, and this never addresses
    anybody — the username does."""
    one = make_user('alex.d')
    two = make_user('alex.r')
    one.display_name = two.display_name = 'Alex'
    _db.session.commit()
    assert one.label == two.label == 'Alex'


# ── signing in is unaffected ───────────────────────────────────────────────

def test_login_still_uses_the_username(client, db):
    user = make_user('kcoleman', password='password123')
    user.display_name = 'Kevin'
    _db.session.commit()

    prime_csrf(client)
    ok = client.post('/auth/login', data={
        'username': 'kcoleman', 'password': 'password123', 'csrf_token': CSRF,
    })
    assert ok.status_code == 302


def test_the_display_name_is_not_a_login(client, db):
    user = make_user('kcoleman', password='password123')
    user.display_name = 'Kevin'
    _db.session.commit()

    prime_csrf(client)
    response = client.post('/auth/login', data={
        'username': 'Kevin', 'password': 'password123', 'csrf_token': CSRF,
    }, environ_base={'REMOTE_ADDR': '198.51.100.9'})
    assert response.status_code == 200        # re-rendered form, not a redirect


# ── where it shows ─────────────────────────────────────────────────────────

def test_the_page_chrome_shows_the_display_name(admin_client, db):
    admin = User.query.filter_by(username='admin').one()
    admin.display_name = 'Kevin C'
    _db.session.commit()
    assert b'Kevin C' in admin_client.get('/').data


def test_the_assignment_menu_shows_display_names(admin_client, db):
    tech = make_user('jsmith')
    tech.display_name = 'Jamie Smith'
    _db.session.commit()
    html = admin_client.get('/work-orders/new').get_data(as_text=True)
    assert 'Jamie Smith' in html


def test_the_work_order_shows_its_assignee_by_display_name(admin_client, db):
    tech = make_user('jsmith')
    tech.display_name = 'Jamie Smith'
    _db.session.commit()
    wo = create_work_order(title='Replace filter', wo_type='planned',
                           assigned_to=tech.id)
    html = admin_client.get(f'/work-orders/{wo.id}').get_data(as_text=True)
    assert 'Jamie Smith' in html


def test_the_admin_list_shows_both(admin_client, db):
    """The one screen where the username still matters."""
    tech = make_user('jsmith')
    tech.display_name = 'Jamie Smith'
    _db.session.commit()
    html = admin_client.get('/admin/users').get_data(as_text=True)
    assert 'Jamie Smith' in html and 'jsmith' in html


# ── managing it ────────────────────────────────────────────────────────────

def test_an_admin_can_set_a_display_name_on_creation(admin_client, db):
    admin_client.post('/admin/users/new', data={
        'csrf_token': CSRF, 'username': 'jsmith', 'email': 'j@example.com',
        'password': 'password123', 'role': 'user', 'display_name': 'Jamie Smith',
    })
    assert User.query.filter_by(username='jsmith').one().display_name == 'Jamie Smith'


def test_an_admin_can_change_it(admin_client, db):
    tech = make_user('jsmith')
    admin_client.post(f'/admin/users/{tech.id}/edit', data={
        'csrf_token': CSRF, 'email': tech.email, 'role': 'user',
        'display_name': 'Jamie S.',
    })
    assert _db.session.get(User, tech.id).display_name == 'Jamie S.'


def test_clearing_it_restores_the_username(admin_client, db):
    tech = make_user('jsmith')
    tech.display_name = 'Jamie Smith'
    _db.session.commit()
    admin_client.post(f'/admin/users/{tech.id}/edit', data={
        'csrf_token': CSRF, 'email': tech.email, 'role': 'user',
        'display_name': '   ',
    })
    refreshed = _db.session.get(User, tech.id)
    assert refreshed.display_name is None      # blank stores NULL, not ''
    assert refreshed.label == 'jsmith'


def test_the_menu_is_ordered_by_what_it_displays(admin_client, db):
    """Ordering by username would look arbitrary once names differ from it."""
    a = make_user('zztop'); a.display_name = 'Aaron'
    b = make_user('aardvark'); b.display_name = 'Zoe'
    _db.session.commit()
    html = admin_client.get('/work-orders/new').get_data(as_text=True)
    assert html.index('Aaron') < html.index('Zoe')


# ── the API keeps using the username ───────────────────────────────────────

def test_the_api_still_reports_the_username(client, db):
    """assigned_to is an addressing key clients POST back, and display names are
    not unique, so this must not become one."""
    from app.models.api_token import ApiToken

    tech = make_user('jsmith')
    tech.display_name = 'Jamie Smith'
    _db.session.commit()
    wo = create_work_order(title='Filter', wo_type='planned', assigned_to=tech.id)

    _, token = ApiToken.issue(tech, 'Test integration')
    _db.session.commit()

    data = client.get(f'/api/v1/work-orders/{wo.wo_number}',
                      headers={'Authorization': f'Bearer {token}'}).get_json()
    assert data['assigned_to'] == 'jsmith'      # not 'Jamie Smith'
