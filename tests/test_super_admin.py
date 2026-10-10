"""The super admin role: Maintenance is theirs alone, and the role cannot be
taken by an admin who is not one.

Three roles, each including the one below: a user works with records, an admin
also manages users and settings, a super admin also has Maintenance — backups,
restore, the database, the sign-in log. The escalation rules matter as much as
the gate: if an admin could grant super admin, or set a super admin's password,
the gate would keep nobody out.
"""
import os
import sqlite3
import subprocess
import sys

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db as _db
from app.models.api_token import ApiToken
from app.models.user import ROLES, User
from tests.conftest import CSRF, make_user, prime_csrf

PASSWORD = 'Password123!'
NEW_PASSWORD = 'Another-password9'


@pytest.fixture
def as_role(client, db, login):
    """Sign in as a fresh account with `role`, and return the client."""
    def sign_in(role, username=None):
        username = username or f'{role}-person'
        make_user(username, role=role, password=PASSWORD)
        login(username, PASSWORD)
        prime_csrf(client)
        return client
    return sign_in


def flashes(client):
    with client.session_transaction() as sess:
        return [message for _category, message in sess.get('_flashes', [])]


# ── the role itself ────────────────────────────────────────────────────────

def test_each_role_includes_the_ones_below(db):
    super_admin = make_user('s', role='super_admin')
    admin = make_user('a', role='admin')
    user = make_user('u', role='user')
    assert (super_admin.is_admin, super_admin.is_super_admin) == (True, True)
    assert (admin.is_admin, admin.is_super_admin) == (True, False)
    assert (user.is_admin, user.is_super_admin) == (False, False)


def test_nobody_can_grant_a_role_above_their_own(db):
    assert make_user('s', role='super_admin').assignable_roles == ROLES
    assert make_user('a', role='admin').assignable_roles == ('admin', 'user')
    assert make_user('u', role='user').assignable_roles == ()


# ── Maintenance is for super admins ────────────────────────────────────────

def maintenance_rules(app):
    """Every route under Maintenance, found rather than listed, so a new one
    cannot be added without this test noticing whether it is guarded."""
    return [rule for rule in app.url_map.iter_rules()
            if rule.rule.startswith(('/admin/maintenance', '/admin/sign-in-attempts'))]


def url_for_rule(rule):
    url = rule.rule
    for argument in rule.arguments:
        url = url.replace(f'<path:{argument}>', 'home-cmms-backup-x.tar.gz')
        url = url.replace(f'<{argument}>', '1')
    return url


def test_there_are_maintenance_routes_to_check(app):
    rules = maintenance_rules(app)
    assert len(rules) >= 14
    assert any(r.rule == '/admin/maintenance' for r in rules)
    assert any(r.rule == '/admin/sign-in-attempts' for r in rules)


@pytest.mark.parametrize('role', ['admin', 'user'])
def test_every_maintenance_route_refuses_anyone_else(app, as_role, role):
    client = as_role(role)
    for rule in maintenance_rules(app):
        url = url_for_rule(rule)
        if 'GET' in rule.methods:
            response = client.get(url)
        else:
            response = client.post(url, data={'csrf_token': CSRF, 'confirm': '1'})
        assert response.status_code == 302, url
        assert response.headers['Location'] == '/', url


def test_a_plain_admin_is_told_why(as_role):
    client = as_role('admin')
    client.get('/admin/maintenance')
    assert 'Super administrator access required.' in flashes(client)


def test_a_super_admin_reaches_maintenance_and_the_log(as_role):
    client = as_role('super_admin')
    assert client.get('/admin/maintenance').status_code == 200
    assert client.get('/admin/sign-in-attempts').status_code == 200


def test_a_refused_action_does_nothing(app, as_role):
    """Not merely a redirect afterwards: the backup is never taken."""
    from app import maintenance
    client = as_role('admin')
    client.post('/admin/maintenance/backup', data={'csrf_token': CSRF})
    assert maintenance.list_backups() == []


@pytest.mark.parametrize('url', ['/admin/users', '/admin/users/new', '/admin/settings'])
def test_admins_keep_users_and_settings(as_role, url):
    assert as_role('admin').get(url).status_code == 200


def sidebar(body):
    start = body.index('<nav')
    return body[start:body.index('</nav>', start)]


def test_only_a_super_admin_is_shown_maintenance(as_role, client):
    nav = sidebar(as_role('super_admin').get('/').get_data(as_text=True))
    assert '/admin/maintenance' in nav and '/admin/users' in nav

    client.get('/auth/logout')
    nav = sidebar(as_role('admin').get('/').get_data(as_text=True))
    assert '/admin/maintenance' not in nav
    assert '/admin/users' in nav and '/admin/settings' in nav


# ── granting the role ─────────────────────────────────────────────────────

def role_options(body):
    import re
    select = re.search(r'<select name="role" id="role">(.*?)</select>', body, re.S).group(1)
    return re.findall(r'<option value="([^"]+)"', select)


def test_the_form_offers_super_admin_only_to_a_super_admin(as_role, client):
    assert role_options(as_role('super_admin').get('/admin/users/new').get_data(as_text=True)) \
        == ['user', 'admin', 'super_admin']
    client.get('/auth/logout')
    assert role_options(as_role('admin').get('/admin/users/new').get_data(as_text=True)) \
        == ['user', 'admin']


def new_user(client, role):
    return client.post('/admin/users/new', data=MultiDict({
        'csrf_token': CSRF, 'username': 'newbie', 'email': 'newbie@example.com',
        'password': NEW_PASSWORD, 'role': role}))


def test_an_admin_cannot_create_a_super_admin(as_role):
    client = as_role('admin')
    response = new_user(client, 'super_admin')
    assert response.status_code == 200          # the form again, with the reason
    assert 'Only a super admin can make another super admin.' in response.get_data(as_text=True)
    assert User.query.filter_by(username='newbie').first() is None


def test_a_super_admin_can(as_role):
    client = as_role('super_admin')
    assert new_user(client, 'super_admin').status_code == 302
    assert User.query.filter_by(username='newbie').one().role == 'super_admin'


def test_an_admin_can_still_create_admins(as_role):
    new_user(as_role('admin'), 'admin')
    assert User.query.filter_by(username='newbie').one().role == 'admin'


def edit(client, user, **fields):
    data = {'csrf_token': CSRF, 'email': user.email, 'role': user.role,
            'display_name': user.display_name or '', 'new_password': ''}
    data.update(fields)
    return client.post(f'/admin/users/{user.id}/edit', data=MultiDict(data))


def test_an_admin_cannot_promote_themselves(as_role):
    client = as_role('admin', 'climber')
    me = User.query.filter_by(username='climber').one()
    response = edit(client, me, role='super_admin')
    assert 'Only a super admin can make another super admin.' in response.get_data(as_text=True)
    assert _db.session.get(User, me.id).role == 'admin'


def test_a_super_admin_can_promote_and_demote(as_role):
    client = as_role('super_admin')
    other = make_user('other', role='admin')
    edit(client, other, role='super_admin')
    assert _db.session.get(User, other.id).role == 'super_admin'
    edit(client, other, role='admin')
    assert _db.session.get(User, other.id).role == 'admin'


def test_the_last_super_admin_cannot_step_down(as_role):
    """Or Maintenance — backups, restore — would be reachable from a shell only."""
    client = as_role('super_admin', 'owner')
    me = User.query.filter_by(username='owner').one()
    response = edit(client, me, role='admin')
    assert 'This is the only super admin' in response.get_data(as_text=True)
    assert _db.session.get(User, me.id).role == 'super_admin'


def test_one_of_two_super_admins_can(as_role):
    client = as_role('super_admin', 'owner')
    make_user('partner', role='super_admin')
    me = User.query.filter_by(username='owner').one()
    edit(client, me, role='admin')
    assert _db.session.get(User, me.id).role == 'admin'


def test_a_deactivated_super_admin_does_not_count(as_role):
    """They cannot sign in to use Maintenance, so they cannot be the reason
    stepping down is allowed."""
    client = as_role('super_admin', 'owner')
    make_user('gone', role='super_admin', active=False)
    me = User.query.filter_by(username='owner').one()
    edit(client, me, role='admin')
    assert _db.session.get(User, me.id).role == 'super_admin'


# ── an admin cannot reach into a super admin's account ────────────────────

@pytest.fixture
def admin_and_owner(as_role):
    owner = make_user('owner', role='super_admin', password=PASSWORD)
    client = as_role('admin')
    return client, owner


def test_an_admin_cannot_open_a_super_admins_edit_page(admin_and_owner):
    client, owner = admin_and_owner
    response = client.get(f'/admin/users/{owner.id}/edit')
    assert response.status_code == 302
    assert 'Only a super admin can change a super admin account.' in flashes(client)


def test_an_admin_cannot_set_a_super_admins_password(admin_and_owner):
    """The direct route into Maintenance: set the password, sign in as them."""
    client, owner = admin_and_owner
    edit(client, owner, new_password=NEW_PASSWORD, email='taken@example.com')
    owner = _db.session.get(User, owner.id)
    assert owner.check_password(PASSWORD)
    assert owner.email == 'owner@example.com'


def test_an_admin_cannot_deactivate_a_super_admin(admin_and_owner):
    client, owner = admin_and_owner
    client.post(f'/admin/users/{owner.id}/toggle', data={'csrf_token': CSRF})
    assert _db.session.get(User, owner.id).is_active


def test_an_admin_cannot_issue_a_token_that_acts_as_a_super_admin(admin_and_owner):
    client, owner = admin_and_owner
    client.post(f'/admin/users/{owner.id}/api-token',
                data={'csrf_token': CSRF, 'token_name': 'mine now'})
    assert ApiToken.query.filter_by(user_id=owner.id).count() == 0


def test_an_admin_cannot_revoke_a_super_admins_token(admin_and_owner):
    client, owner = admin_and_owner
    token, _plaintext = ApiToken.issue(owner, 'Home Assistant')
    _db.session.commit()
    client.post(f'/admin/users/{owner.id}/api-token/{token.id}/revoke',
                data={'csrf_token': CSRF})
    assert _db.session.get(ApiToken, token.id) is not None


def row_for(body, username):
    import re
    for row in re.findall(r'<tr>(.*?)</tr>', body, re.S):
        if username in row:
            return row
    raise AssertionError(f'no row for {username}')


def test_the_list_offers_an_admin_no_controls_on_a_super_admin(admin_and_owner):
    client, owner = admin_and_owner
    make_user('regular', role='user')
    body = client.get('/admin/users').get_data(as_text=True)
    assert f'/admin/users/{owner.id}/edit' not in row_for(body, 'owner')
    assert '/toggle' not in row_for(body, 'owner')
    assert 'Super Admin' in row_for(body, 'owner')
    regular = User.query.filter_by(username='regular').one()
    assert f'/admin/users/{regular.id}/edit' in row_for(body, 'regular')


def test_a_super_admin_gets_controls_on_everyone(as_role):
    client = as_role('super_admin', 'owner')
    other = make_user('partner', role='super_admin')
    body = client.get('/admin/users').get_data(as_text=True)
    assert f'/admin/users/{other.id}/edit' in row_for(body, 'partner')
    assert f'/admin/users/{other.id}/toggle' in row_for(body, 'partner')


def test_the_role_filter_knows_super_admins(as_role):
    client = as_role('super_admin', 'owner')
    make_user('helper', role='admin')
    body = client.get('/admin/users?role=super_admin').get_data(as_text=True)
    assert 'owner' in body and 'helper' not in body
    assert '<option value="super_admin" selected>Super Admin</option>' in body


# ── from the command line ─────────────────────────────────────────────────

def run_create_admin(app, *args):
    environment = {
        **os.environ, 'SCHEDULER_ENABLED': '0', 'SECRET_KEY': 'test-secret',
        'DATABASE_URL': app.config['SQLALCHEMY_DATABASE_URI'],
        'UPLOAD_FOLDER': app.config['UPLOAD_FOLDER'],
    }
    return subprocess.run([sys.executable, 'create_admin.py', *args],
                          capture_output=True, text=True, env=environment, timeout=60)


def role_of(username):
    _db.session.expire_all()
    return User.query.filter_by(username=username).one().role


def test_promote_makes_an_existing_account_a_super_admin(app, db):
    make_user('kevin', role='user')
    result = run_create_admin(app, '--promote', 'kevin')
    assert result.returncode == 0, result.stderr
    assert "'kevin' is now a super admin." in result.stdout
    assert role_of('kevin') == 'super_admin'


def test_promote_twice_changes_nothing(app, db):
    make_user('kevin', role='super_admin')
    result = run_create_admin(app, '--promote', 'kevin')
    assert result.returncode == 0
    assert 'already a super admin' in result.stdout


def test_promote_refuses_an_unknown_account(app, db):
    result = run_create_admin(app, '--promote', 'nobody')
    assert result.returncode == 1
    assert "no account called 'nobody'" in result.stderr


def test_promote_says_when_the_account_cannot_sign_in(app, db):
    """Said, not changed: deactivating someone was a decision of its own."""
    make_user('kevin', role='admin', active=False)
    result = run_create_admin(app, '--promote', 'kevin')
    assert result.returncode == 0
    assert 'deactivated' in result.stderr
    assert role_of('kevin') == 'super_admin'
    assert not User.query.filter_by(username='kevin').one().is_active


# ── upgrading an existing instance ────────────────────────────────────────

def flask_db(db_path, *args):
    environment = {**os.environ, 'SCHEDULER_ENABLED': '0', 'SECRET_KEY': 'test-secret',
                   'DATABASE_URL': f'sqlite:///{db_path}', 'FLASK_APP': 'run.py'}
    result = subprocess.run([sys.executable, '-m', 'flask', 'db', *args],
                            capture_output=True, text=True, env=environment, timeout=120)
    assert result.returncode == 0, result.stderr
    return result


def roles_in(db_path):
    with sqlite3.connect(db_path) as connection:
        return dict(connection.execute('select username, role from users'))


def test_upgrading_makes_every_admin_a_super_admin(tmp_path):
    """Everyone who could use Maintenance before the upgrade still can, and
    nobody else gains it — so upgrading changes nobody's access."""
    db_path = tmp_path / 'old.db'
    flask_db(db_path, 'upgrade', '84b7eef551cb')
    with sqlite3.connect(db_path) as connection:
        for name, role in (('owner', 'admin'), ('helper', 'admin'), ('member', 'user')):
            connection.execute(
                "insert into users (username, email, password_hash, role, is_active) "
                "values (?, ?, 'x', ?, 1)", (name, f'{name}@example.com', role))

    flask_db(db_path, 'upgrade')
    assert roles_in(db_path) == {'owner': 'super_admin', 'helper': 'super_admin',
                                 'member': 'user'}

    flask_db(db_path, 'downgrade', '84b7eef551cb')
    assert roles_in(db_path) == {'owner': 'admin', 'helper': 'admin', 'member': 'user'}
