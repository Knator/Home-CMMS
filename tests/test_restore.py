"""Restoring a backup — the one operation that destroys everything already there.

Three things have to hold, and each is checked here rather than trusted:
  * a hostile archive cannot write outside the instance;
  * an archive that is not a Home CMMS backup is refused *before* anything is
    replaced, so a wrong file costs nothing;
  * a real backup actually comes back — database rows and uploaded files both.
"""
import io
import os
import sqlite3
import tarfile

import pytest
from flask_migrate import stamp

from app import maintenance
from app.maintenance import RestoreError
from app.models.asset import Asset
from app.models.user import User
from app.services import create_asset
from tests.conftest import CSRF, make_user, prime_csrf


@pytest.fixture
def stamped(app):
    """Give the throwaway database an alembic_version, as a real one has.

    conftest builds the schema with create_all(), which does not write one, and
    a backup without it is (correctly) rejected as not a Home CMMS backup.
    """
    stamp()
    return app


def write_upload(app, relative, content=b'file-content'):
    path = os.path.join(app.config['UPLOAD_FOLDER'], relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(content)
    return path


def make_archive(path, members):
    """Build a tar.gz from {name: bytes-or-TarInfo-tuple}."""
    with tarfile.open(path, 'w:gz') as tar:
        for name, payload in members.items():
            data = payload if isinstance(payload, bytes) else b''
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


# ── archive validation ─────────────────────────────────────────────────────

@pytest.mark.parametrize('member', [
    '/etc/passwd',
    '../../etc/cron.d/payload',
    'uploads/../../../home/victim/.ssh/authorized_keys',
])
def test_archive_escaping_the_instance_is_refused(stamped, tmp_path, member):
    archive = str(tmp_path / 'evil.tar.gz')
    make_archive(archive, {maintenance.DB_MEMBER: b'x', member: b'pwned'})

    with pytest.raises(RestoreError) as excinfo:
        maintenance.inspect_backup(archive)
    assert 'path' in str(excinfo.value).lower()


def test_archive_containing_a_symlink_is_refused(stamped, tmp_path):
    archive = str(tmp_path / 'link.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        info = tarfile.TarInfo(maintenance.DB_MEMBER)
        info.size = 1
        tar.addfile(info, io.BytesIO(b'x'))
        link = tarfile.TarInfo('uploads/escape')
        link.type = tarfile.SYMTYPE
        link.linkname = '/etc/passwd'
        tar.addfile(link)

    with pytest.raises(RestoreError, match='link'):
        maintenance.inspect_backup(archive)


def test_archive_with_unexpected_entries_is_refused(stamped, tmp_path):
    archive = str(tmp_path / 'extra.tar.gz')
    make_archive(archive, {maintenance.DB_MEMBER: b'x', 'config.py': b'BAD = 1'})

    with pytest.raises(RestoreError, match='unexpected'):
        maintenance.inspect_backup(archive)


def test_a_file_that_is_not_an_archive_is_refused(stamped, tmp_path):
    plain = tmp_path / 'notes.txt'
    plain.write_text('not a backup')

    with pytest.raises(RestoreError, match='not a .tar.gz'):
        maintenance.inspect_backup(str(plain))


def test_archive_without_a_database_is_refused(stamped, tmp_path):
    archive = str(tmp_path / 'nodb.tar.gz')
    make_archive(archive, {'uploads/asset/1/photo.jpg': b'jpeg'})

    with pytest.raises(RestoreError, match='no home_cmms.db'):
        maintenance.inspect_backup(archive)


def test_archive_whose_database_is_not_ours_is_refused(stamped, tmp_path):
    other = tmp_path / 'other.db'
    connection = sqlite3.connect(str(other))
    connection.execute('create table notes (id integer)')
    connection.commit()
    connection.close()

    archive = str(tmp_path / 'other.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(str(other), arcname=maintenance.DB_MEMBER)

    with pytest.raises(RestoreError, match='schema version'):
        maintenance.inspect_backup(archive)


def test_inspect_reports_what_the_backup_holds(stamped, app):
    make_user('alice', role='admin')
    create_asset(name='Furnace')
    write_upload(app, 'asset/1/manual.pdf')

    created = maintenance.create_backup()
    summary = maintenance.inspect_backup(
        os.path.join(maintenance.backup_dir(), created['name']))

    assert summary['counts']['users'] == 1
    assert summary['counts']['assets'] == 1
    assert summary['attachment_files'] == 1
    assert summary['revision']


# ── restoring ──────────────────────────────────────────────────────────────

def test_restore_brings_back_records_and_files(stamped, app):
    make_user('alice', role='admin')
    create_asset(name='Furnace')
    write_upload(app, 'asset/1/manual.pdf', b'the original manual')

    created = maintenance.create_backup()
    archive = os.path.join(maintenance.backup_dir(), created['name'])

    # Diverge: delete the asset and the file, add a different user.
    Asset.query.delete()
    User.query.delete()
    make_user('mallory', role='admin')
    os.remove(os.path.join(app.config['UPLOAD_FOLDER'], 'asset/1/manual.pdf'))

    maintenance.restore_backup(archive)

    assert [a.name for a in Asset.query.all()] == ['Furnace']
    assert [u.username for u in User.query.all()] == ['alice']
    restored = os.path.join(app.config['UPLOAD_FOLDER'], 'asset/1/manual.pdf')
    assert open(restored, 'rb').read() == b'the original manual'


def test_restore_archives_the_previous_state_first(stamped, app):
    make_user('alice', role='admin')
    created = maintenance.create_backup()
    archive = os.path.join(maintenance.backup_dir(), created['name'])

    make_user('bob')
    summary = maintenance.restore_backup(archive)

    safety = summary['safety_copy']
    assert safety and safety.startswith(maintenance.SAFETY_PREFIX)
    # The undo copy holds the state that was just discarded: two users, not one.
    undo = maintenance.inspect_backup(os.path.join(maintenance.backup_dir(), safety))
    assert undo['counts']['users'] == 2


def test_restore_can_skip_the_safety_copy(stamped, app):
    make_user('alice', role='admin')
    created = maintenance.create_backup()

    summary = maintenance.restore_backup(
        os.path.join(maintenance.backup_dir(), created['name']),
        take_safety_copy=False)

    assert summary['safety_copy'] is None


def test_a_refused_archive_changes_nothing(stamped, app, tmp_path):
    make_user('alice', role='admin')
    bad = str(tmp_path / 'bad.tar.gz')
    make_archive(bad, {maintenance.DB_MEMBER: b'x', '../escape': b'no'})

    with pytest.raises(RestoreError):
        maintenance.restore_backup(bad)

    assert User.query.count() == 1
    # Validation happens before the safety copy, so a rejected file leaves no
    # debris in the backups directory either.
    assert maintenance.list_backups() == []


def test_restore_works_when_the_current_database_is_unreadable(stamped, app):
    """The case restore exists for: the live database is broken.

    A junk -wal makes SQLite refuse to open the database at all, so the safety
    copy cannot be taken. That must not stop the restore — and the leftover
    sidecar must go, or it would graft the dead write-ahead log onto the file
    that replaces it.
    """
    make_user('alice', role='admin')
    created = maintenance.create_backup()

    db_path = maintenance.database_path()
    with open(f'{db_path}-wal', 'wb') as handle:
        handle.write(b'stale write-ahead log')

    summary = maintenance.restore_backup(
        os.path.join(maintenance.backup_dir(), created['name']))

    assert summary['safety_copy'] is None
    assert summary['safety_error']
    assert [u.username for u in User.query.all()] == ['alice']
    assert not os.path.exists(f'{db_path}-wal') or \
        open(f'{db_path}-wal', 'rb').read() != b'stale write-ahead log'


def test_sidecars_from_the_replaced_database_do_not_survive(stamped, app):
    make_user('alice', role='admin')
    created = maintenance.create_backup()

    db_path = maintenance.database_path()
    maintenance.restore_backup(
        os.path.join(maintenance.backup_dir(), created['name']),
        take_safety_copy=False)

    for suffix in ('-wal', '-shm'):
        assert not os.path.exists(f'{db_path}{suffix}')


# ── backup naming and retention ────────────────────────────────────────────

def test_safety_copies_are_listed_but_never_pruned(stamped, app):
    make_user('alice', role='admin')
    maintenance.create_backup()
    created = maintenance.create_backup()
    maintenance.restore_backup(os.path.join(maintenance.backup_dir(), created['name']))

    names = [b['name'] for b in maintenance.list_backups()]
    assert any(n.startswith(maintenance.SAFETY_PREFIX) for n in names)

    maintenance.prune_backups(keep=0)
    remaining = maintenance.list_backups()
    assert remaining and all(b['automatic'] for b in remaining)


@pytest.mark.parametrize('name,ok', [
    ('home-cmms-backup-20260101-000000.tar.gz', True),
    ('pre-restore-20260101-000000.tar.gz', True),
    ('../../etc/passwd', False),
    ('home-cmms-backup-x/../../y.tar.gz', False),
    ('random.tar.gz', False),
    ('home-cmms-backup-20260101-000000.zip', False),
])
def test_backup_names_are_validated(name, ok):
    assert maintenance.is_backup_name(name) is ok


# ── the maintenance page ───────────────────────────────────────────────────

def restore_post(client, **data):
    data.setdefault('csrf_token', CSRF)
    return client.post('/admin/maintenance/restore', data=data,
                       follow_redirects=False)


def test_restore_requires_an_administrator(stamped, client, login, app):
    make_user('bystander')
    create_asset(name='Marker')
    created = maintenance.create_backup()
    Asset.query.delete()

    login('bystander')
    prime_csrf(client)
    response = restore_post(client, name=created['name'], confirm='1')

    # Bounced by @admin_required, and the backup it named was not applied.
    assert response.status_code == 302
    assert '/admin/maintenance' not in response.headers['Location']
    assert Asset.query.count() == 0


def test_restore_requires_the_confirmation_box(stamped, client, login, app):
    make_user('admin', role='admin')
    create_asset(name='Marker')
    created = maintenance.create_backup()
    Asset.query.delete()

    login('admin')
    prime_csrf(client)
    response = restore_post(client, name=created['name'])

    assert response.status_code == 302
    assert '/admin/maintenance' in response.headers['Location']
    # Nothing ran: the asset is still gone and no undo copy was written.
    assert Asset.query.count() == 0
    assert not any(b['automatic'] for b in maintenance.list_backups())


@pytest.mark.parametrize('name', [
    '../../../etc/passwd',
    '/etc/passwd',
    'home-cmms-backup-../../escape.tar.gz',
])
def test_restore_rejects_a_name_outside_the_backups_folder(
        stamped, client, login, app, name):
    make_user('admin', role='admin')
    create_asset(name='Marker')
    login('admin')
    prime_csrf(client)

    response = restore_post(client, name=name, confirm='1')

    assert response.status_code == 302
    assert Asset.query.count() == 1
    assert not any(b['automatic'] for b in maintenance.list_backups())


def test_restore_from_the_page_replaces_the_data_and_signs_everyone_out(
        stamped, client, login, app):
    make_user('admin', role='admin')
    create_asset(name='Boiler')
    created = maintenance.create_backup()

    Asset.query.delete()
    _ = create_asset(name='Something else entirely')

    login('admin')
    prime_csrf(client)
    response = restore_post(client, name=created['name'], confirm='1')

    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']
    assert [a.name for a in Asset.query.all()] == ['Boiler']

    # The signing key changed, so the cookie the client still holds is refused
    # and the session it names is gone.
    followed = client.get('/', follow_redirects=False)
    assert '/auth/login' in followed.headers.get('Location', '')


def test_restore_accepts_an_uploaded_archive(stamped, client, login, app):
    make_user('admin', role='admin')
    create_asset(name='Water Heater')
    created = maintenance.create_backup()
    archive_bytes = open(
        os.path.join(maintenance.backup_dir(), created['name']), 'rb').read()

    Asset.query.delete()
    db_session_assets = Asset.query.count()
    assert db_session_assets == 0

    login('admin')
    prime_csrf(client)
    response = client.post('/admin/maintenance/restore', data={
        'csrf_token': CSRF,
        'confirm': '1',
        'archive': (io.BytesIO(archive_bytes), 'my-backup.tar.gz'),
    }, content_type='multipart/form-data')

    assert response.status_code == 302
    assert [a.name for a in Asset.query.all()] == ['Water Heater']


# ── the first-run setup screen ─────────────────────────────────────────────

def test_setup_offers_restore_while_no_users_exist(stamped, client):
    response = client.get('/setup')
    assert response.status_code == 200
    assert b'Restore from a backup instead' in response.data


def test_setup_restore_brings_an_instance_back_and_closes_setup(
        stamped, client, app):
    make_user('alice', role='admin')
    create_asset(name='Dishwasher')
    write_upload(app, 'asset/1/manual.pdf', b'manual bytes')
    created = maintenance.create_backup()
    archive_bytes = open(
        os.path.join(maintenance.backup_dir(), created['name']), 'rb').read()

    # Back to a fresh instance: no users, so setup is open again.
    Asset.query.delete()
    User.query.delete()
    from app.extensions import db as _db
    _db.session.commit()

    prime_csrf(client)
    response = client.post('/setup/restore', data={
        'csrf_token': CSRF,
        'archive': (io.BytesIO(archive_bytes), 'backup.tar.gz'),
    }, content_type='multipart/form-data')

    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']
    assert [u.username for u in User.query.all()] == ['alice']
    assert [a.name for a in Asset.query.all()] == ['Dishwasher']

    # Setup is closed now that an account exists.
    assert '/auth/login' in client.get('/setup').headers.get('Location', '')


def test_setup_restore_takes_no_safety_copy(stamped, client, app):
    make_user('alice', role='admin')
    created = maintenance.create_backup()
    archive_bytes = open(
        os.path.join(maintenance.backup_dir(), created['name']), 'rb').read()
    User.query.delete()
    from app.extensions import db as _db
    _db.session.commit()

    prime_csrf(client)
    client.post('/setup/restore', data={
        'csrf_token': CSRF,
        'archive': (io.BytesIO(archive_bytes), 'backup.tar.gz'),
    }, content_type='multipart/form-data')

    # Nothing was worth preserving, so the backups list has no undo copy in it.
    assert not any(b['automatic'] for b in maintenance.list_backups())


def test_setup_restore_of_a_userless_backup_leaves_setup_open(
        stamped, client, app):
    """A backup with no accounts would lock the instance out of both paths."""
    create_asset(name='Sump Pump')
    created = maintenance.create_backup()
    archive_bytes = open(
        os.path.join(maintenance.backup_dir(), created['name']), 'rb').read()

    prime_csrf(client)
    response = client.post('/setup/restore', data={
        'csrf_token': CSRF,
        'archive': (io.BytesIO(archive_bytes), 'backup.tar.gz'),
    }, content_type='multipart/form-data', follow_redirects=True)

    assert b'no user accounts' in response.data
    assert b'Create administrator' in response.data


def test_setup_restore_is_closed_once_an_account_exists(stamped, client, app):
    make_user('alice', role='admin')
    created = maintenance.create_backup()
    archive_bytes = open(
        os.path.join(maintenance.backup_dir(), created['name']), 'rb').read()

    prime_csrf(client)
    response = client.post('/setup/restore', data={
        'csrf_token': CSRF,
        'archive': (io.BytesIO(archive_bytes), 'backup.tar.gz'),
    }, content_type='multipart/form-data')

    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']
    # Refused before doing anything: the archive was never opened, so the only
    # thing in the backups folder is still the one backup that was made.
    assert [b['name'] for b in maintenance.list_backups()] == [created['name']]
