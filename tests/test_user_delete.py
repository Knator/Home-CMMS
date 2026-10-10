"""Deleting users: only an account with no history, and never one whose id
could be handed to somebody else.

The rule is the one assets and locations follow — anything that has left a mark
on a record is deactivated, not deleted — and these pin each kind of mark. The
id half matters as much: cookies carry nothing but the user id, so an id given
out twice would sign a deleted account's browser in as whoever got it next.
"""
import os
import sqlite3
import subprocess
import sys

import pytest

from app import settings as app_settings
from app.extensions import db as _db
from app.models.api_token import ApiToken
from app.models.attachment import Attachment
from app.models.auth_attempt import AuthAttempt
from app.models.job_plan import JobPlan
from app.models.pm import PM
from app.models.setting import Setting
from app.models.user import User
from app.models.work_order import WorkOrder
from app.services import (
    UserInUse, archive_work_order, create_work_order, delete_user, user_delete_blockers,
)
from tests.conftest import CSRF, make_user, prime_csrf

PASSWORD = 'Password123!'


@pytest.fixture
def admin(client, db, login):
    make_user('boss', role='super_admin', password=PASSWORD)
    login('boss', PASSWORD)
    prime_csrf(client)
    return client


def delete(client, user_id, query=''):
    return client.post(f'/admin/users/{user_id}/delete{query}', data={'csrf_token': CSRF})


def exists(user_id):
    _db.session.expire_all()
    return _db.session.get(User, user_id) is not None


def flashes(client):
    with client.session_transaction() as sess:
        return [message for _category, message in sess.get('_flashes', [])]


# ── what counts as history ────────────────────────────────────────────────

def _created_work_order(user):
    create_work_order(title='Leak', created_by=user.id)


def _assigned_work_order(user):
    create_work_order(title='Leak', assigned_to=user.id)


def _archived_work_order(user):
    from datetime import date
    wo = create_work_order(title='Leak', created_by=user.id, status='completed',
                           completed_date=date.today())
    archive_work_order(wo)


def _pm(user):
    from datetime import date
    _db.session.add(PM(name='Filter', interval_days=30, next_due_date=date.today(),
                       created_by=user.id))
    _db.session.commit()


def _job_plan(user):
    _db.session.add(JobPlan(name='Service', created_by=user.id))
    _db.session.commit()


def _attachment(user):
    _db.session.add(Attachment(entity_type='asset', entity_id=1, stored_filename='x_manual.pdf',
                               original_filename='manual.pdf', uploaded_by=user.id))
    _db.session.commit()


@pytest.mark.parametrize('leave_a_mark, reason', [
    (_created_work_order, 'created 1 work order'),
    (_assigned_work_order, 'is assigned 1 work order'),
    # Archiving freezes the names a record shows, but keeps its links.
    (_archived_work_order, 'created 1 work order'),
    (_pm, 'created 1 PM schedule'),
    (_job_plan, 'created 1 job plan'),
    (_attachment, 'uploaded 1 file'),
])
def test_any_mark_on_a_record_blocks_deletion(admin, leave_a_mark, reason):
    victim = make_user('helper')
    leave_a_mark(victim)
    assert user_delete_blockers(victim) == [reason]

    delete(admin, victim.id)
    assert exists(victim.id)
    assert any(reason in m and 'Deactivate it instead' in m for m in flashes(admin))


def test_every_reason_is_reported_together(db):
    victim = make_user('helper')
    for _ in range(2):
        create_work_order(title='Leak', created_by=victim.id, assigned_to=victim.id)
    _attachment(victim)
    assert user_delete_blockers(victim) == [
        'created 2 work orders', 'is assigned 2 work orders', 'uploaded 1 file']


def test_the_service_refuses_whoever_calls_it(db):
    """Not only the page: a bare ORM delete would blank "created by" on the work
    order and carry on, so the refusal has to live where the delete does."""
    victim = make_user('helper')
    wo = create_work_order(title='Leak', created_by=victim.id)
    with pytest.raises(UserInUse) as refused:
        delete_user(victim)
    assert refused.value.blockers == ['created 1 work order']
    _db.session.expire_all()
    assert _db.session.get(WorkOrder, wo.id).created_by == victim.id


# ── what goes, and what stays ─────────────────────────────────────────────

def test_an_account_with_no_history_is_deleted(admin):
    victim = make_user('typo')
    response = delete(admin, victim.id)
    assert not exists(victim.id)
    assert response.headers['Location'].startswith('/admin/users')
    assert "User 'typo' deleted." in flashes(admin)


def test_its_tokens_go_with_it(admin):
    victim = make_user('typo')
    ApiToken.issue(victim, 'Home Assistant')
    _db.session.commit()
    delete(admin, victim.id)
    assert ApiToken.query.count() == 0


def test_having_changed_a_setting_does_not_block_it(admin):
    """'Last changed by' is not record history; it is cleared instead."""
    victim = make_user('tester-admin', role='admin')
    app_settings.set_value('auto_archive_days', 30, user_id=victim.id)
    _db.session.commit()
    delete(admin, victim.id)
    assert not exists(victim.id)
    row = _db.session.get(Setting, 'auto_archive_days')
    assert row.updated_by is None and row.value is not None


def test_the_sign_in_log_is_left_alone(admin):
    victim = make_user('typo')
    _db.session.add(AuthAttempt(identifier='typo', ip_address='10.0.0.5', successful=False))
    _db.session.commit()
    delete(admin, victim.id)
    assert AuthAttempt.query.filter_by(identifier='typo').count() == 1


def test_the_list_filters_come_back(admin):
    victim = make_user('typo')
    response = delete(admin, victim.id, '?role=user&q=ty')
    location = response.headers['Location']
    assert 'role=user' in location and 'q=ty' in location


# ── who may delete whom ───────────────────────────────────────────────────

def test_nobody_deletes_themselves(admin):
    me = User.query.filter_by(username='boss').one()
    delete(admin, me.id)
    assert exists(me.id)
    assert 'You cannot delete your own account.' in flashes(admin)


def test_an_admin_cannot_delete_a_super_admin(client, db, login):
    owner = make_user('owner', role='super_admin')
    make_user('helper', role='admin', password=PASSWORD)
    login('helper', PASSWORD)
    prime_csrf(client)
    delete(client, owner.id)
    assert exists(owner.id)


def test_a_super_admin_can_delete_another(admin):
    other = make_user('spare', role='super_admin')
    delete(admin, other.id)
    assert not exists(other.id)


def test_a_plain_user_cannot_delete_anyone(client, db, login):
    victim = make_user('typo')
    make_user('member', password=PASSWORD)
    login('member', PASSWORD)
    prime_csrf(client)
    delete(client, victim.id)
    assert exists(victim.id)


def test_it_needs_the_csrf_token(admin):
    victim = make_user('typo')
    response = admin.post(f'/admin/users/{victim.id}/delete', data={'csrf_token': 'wrong'})
    assert response.status_code == 403
    assert exists(victim.id)


# ── the edit page ─────────────────────────────────────────────────────────

def edit_page(client, user):
    return client.get(f'/admin/users/{user.id}/edit').get_data(as_text=True)


def test_the_page_offers_delete_when_it_is_allowed(admin):
    victim = make_user('typo')
    body = edit_page(admin, victim)
    assert f'action="/admin/users/{victim.id}/delete"' in body
    assert "data-confirm=\"Delete the account 'typo'? This cannot be undone.\"" in body


def test_the_page_explains_why_not_and_offers_deactivate(admin):
    victim = make_user('helper')
    _created_work_order(victim)
    body = edit_page(admin, victim)
    assert '/delete"' not in body
    assert 'It cannot be deleted because it created 1 work order.' in body
    assert f'action="/admin/users/{victim.id}/toggle"' in body


def test_a_deactivated_account_with_history_is_not_offered_deactivate_again(admin):
    victim = make_user('helper', active=False)
    _created_work_order(victim)
    body = edit_page(admin, victim)
    assert '/toggle"' not in body
    assert 'already deactivated' in body


def test_your_own_page_offers_neither(admin):
    me = User.query.filter_by(username='boss').one()
    body = edit_page(admin, me)
    assert '/delete"' not in body and 'This account has history' not in body


def test_the_delete_form_carries_the_list_filters(admin):
    victim = make_user('typo')
    body = admin.get(f'/admin/users/{victim.id}/edit?role=user').get_data(as_text=True)
    assert f'action="/admin/users/{victim.id}/delete?role=user"' in body


def test_a_quoted_username_cannot_break_out_of_the_confirmation(admin):
    """The username goes in data-confirm, which nothing parses as code."""
    victim = make_user("o'brien\"><script>x</script>")
    body = edit_page(admin, victim)
    assert '<script>x</script>' not in body


# ── ids are never handed out twice ───────────────────────────────────────

def test_a_deleted_accounts_id_is_not_reused(db):
    make_user('owner')
    newest = make_user('temp')
    old_id = newest.id
    delete_user(newest)
    assert make_user('newcomer').id > old_id


def test_a_deleted_accounts_browser_is_not_signed_in_as_the_next_account(app, admin):
    """The attack the id rule exists for: sign in as the newest account, have
    it deleted, create another — the old cookie must not now belong to them."""
    make_user('temp', password=PASSWORD)
    stale = app.test_client()
    prime_csrf(stale)
    stale.post('/auth/login', data={'username': 'temp', 'password': PASSWORD,
                                    'csrf_token': CSRF, 'remember': '1'})
    assert stale.get('/').status_code == 200

    delete(admin, User.query.filter_by(username='temp').one().id)
    make_user('newcomer', role='super_admin', password=PASSWORD)

    response = stale.get('/')
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']


# ── the migration ────────────────────────────────────────────────────────

def flask_db(db_path, *args):
    environment = {**os.environ, 'SCHEDULER_ENABLED': '0', 'SECRET_KEY': 'test-secret',
                   'DATABASE_URL': f'sqlite:///{db_path}', 'FLASK_APP': 'run.py'}
    result = subprocess.run([sys.executable, '-m', 'flask', 'db', *args],
                            capture_output=True, text=True, env=environment, timeout=120)
    assert result.returncode == 0, result.stderr


def test_the_migration_keeps_every_id_and_reference(tmp_path):
    db_path = tmp_path / 'old.db'
    flask_db(db_path, 'upgrade', 'b3c1d7e9f2a4')
    with sqlite3.connect(db_path) as connection:
        for user_id, name in ((3, 'owner'), (7, 'helper')):
            connection.execute(
                "insert into users (id, username, email, password_hash, role, is_active, "
                "created_at) values (?, ?, ?, 'x', 'user', 1, '2026-10-01 12:00:00.000001')",
                (user_id, name, f'{name}@example.com'))
        connection.execute(
            "insert into work_orders (wo_number, title, wo_type, status, priority, "
            "created_by, assigned_to) values ('WO-2026-00001', 'Leak', 'planned', 'open', "
            "'medium', 3, 7)")

    flask_db(db_path, 'upgrade')

    with sqlite3.connect(db_path) as connection:
        schema = connection.execute(
            "select sql from sqlite_master where name = 'users'").fetchone()[0]
        assert 'AUTOINCREMENT' in schema
        assert connection.execute('select id, username from users order by id').fetchall() \
            == [(3, 'owner'), (7, 'helper')]
        # Text, not mangled into a number by a cast.
        assert connection.execute('select created_at from users where id = 3').fetchone()[0] \
            == '2026-10-01 12:00:00.000001'
        assert connection.execute(
            'select created_by, assigned_to from work_orders').fetchone() == (3, 7)
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []

        # Numbering carries on past the highest id, and never steps back.
        connection.execute('delete from users where id = 7')
        connection.execute("insert into users (username, email, password_hash, role, "
                           "is_active) values ('newcomer', 'n@example.com', 'x', 'user', 1)")
        assert connection.execute(
            "select id from users where username = 'newcomer'").fetchone()[0] == 8

    flask_db(db_path, 'downgrade', 'b3c1d7e9f2a4')
    with sqlite3.connect(db_path) as connection:
        schema = connection.execute(
            "select sql from sqlite_master where name = 'users'").fetchone()[0]
        assert 'AUTOINCREMENT' not in schema
        assert [row[0] for row in connection.execute('select id from users order by id')] \
            == [3, 8]
