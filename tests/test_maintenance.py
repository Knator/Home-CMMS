"""The admin maintenance page: backups, integrity, database housekeeping."""
import io
import os
import tarfile

import pytest

from app import maintenance
from app.models.attachment import Attachment
from app.models.location import Location
from app.services import create_location, create_asset, create_work_order
from tests.conftest import CSRF, make_user


@pytest.fixture
def admin_login(client, app, login):
    make_user('boss', role='admin')
    login('boss')
    return client


def upload(client, asset, name='manual.pdf'):
    return client.post(f'/assets/{asset.id}/attachments',
                       data={'file': (io.BytesIO(b'%PDF data'), name), 'csrf_token': CSRF},
                       content_type='multipart/form-data')


# ── access control ─────────────────────────────────────────────────────────

def test_page_requires_admin(client, db, user, login):
    login()
    response = client.get('/admin/maintenance')
    assert response.status_code == 302
    assert '/admin' not in response.headers['Location']


def test_page_requires_login(client, db):
    response = client.get('/admin/maintenance')
    assert '/auth/login' in response.headers['Location']


def test_destructive_actions_need_csrf(client, db, admin_login):
    for path in ('/admin/maintenance/backup', '/admin/maintenance/vacuum',
                 '/admin/maintenance/clean-storage', '/admin/maintenance/clear-thumbnails'):
        assert client.post(path, data={}).status_code == 403, path


# ── status ─────────────────────────────────────────────────────────────────

def test_status_reports_sizes_and_counts(app, db, user):
    with app.app_context():
        status = maintenance.system_status()
    assert status['database_path'].endswith('.db')
    assert status['disk_total'] > 0
    assert 'Work Orders' in status['counts']
    assert status['counts']['Users'] >= 1


def test_page_renders_the_status(client, db, admin_login):
    body = client.get('/admin/maintenance').get_data(as_text=True)
    assert 'Maintenance' in body
    assert 'PM Scheduler' in body
    assert 'Backups' in body
    assert 'Storage Integrity' in body


def test_scheduler_status_when_disabled(app):
    """Tests run with the scheduler off; the page must say so rather than break."""
    with app.app_context():
        status = maintenance.scheduler_status()
    assert status['running'] is False
    assert status['reason']


def test_run_pm_check_from_the_page(client, db, admin_login):
    from datetime import date
    from app.models.pm import PM
    from app.models.work_order import WorkOrder

    db.session.add(PM(name='Due now', interval_days=30, next_due_date=date.today()))
    db.session.commit()

    client.post('/admin/maintenance/run-pm-check', data={'csrf_token': CSRF})
    assert WorkOrder.query.count() == 1


# ── backups ────────────────────────────────────────────────────────────────

def test_backup_contains_the_database_and_uploads(client, app, db, admin_login):
    asset = create_asset(name='Furnace')
    upload(client, asset, 'manual.pdf')

    client.post('/admin/maintenance/backup', data={'csrf_token': CSRF})

    with app.app_context():
        backups = maintenance.list_backups()
        assert len(backups) == 1
        path = os.path.join(maintenance.backup_dir(), backups[0]['name'])

    with tarfile.open(path) as tar:
        names = tar.getnames()
    assert 'home_cmms.db' in names
    assert any(n.startswith('uploads/asset/') for n in names)


def test_backup_database_is_readable_and_current(client, app, db, admin_login, tmp_path):
    """VACUUM INTO rather than a file copy, so WAL content is included."""
    import sqlite3

    loc = create_location(name='Distinctive Name')
    db.session.add(loc)
    db.session.commit()

    client.post('/admin/maintenance/backup', data={'csrf_token': CSRF})
    with app.app_context():
        path = os.path.join(maintenance.backup_dir(), maintenance.list_backups()[0]['name'])

    with tarfile.open(path) as tar:
        tar.extract('home_cmms.db', tmp_path, filter='data')
    restored = sqlite3.connect(tmp_path / 'home_cmms.db')
    names = [r[0] for r in restored.execute('select name from locations')]
    assert 'Distinctive Name' in names


def test_thumbnails_are_excluded_from_backups(client, app, db, admin_login):
    """They rebuild on demand, so shipping them just inflates the archive."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (200, 200)).save(buf, 'JPEG')
    asset = create_asset(name='Furnace')
    client.post(f'/assets/{asset.id}/attachments',
                data={'file': (io.BytesIO(buf.getvalue()), 'p.jpg'), 'csrf_token': CSRF},
                content_type='multipart/form-data')
    att = Attachment.query.one()
    client.get(f'/attachments/{att.id}/thumbnail')      # force one into the cache

    client.post('/admin/maintenance/backup', data={'csrf_token': CSRF})
    with app.app_context():
        path = os.path.join(maintenance.backup_dir(), maintenance.list_backups()[0]['name'])
    with tarfile.open(path) as tar:
        names = tar.getnames()
    assert not any('.thumbnails' in n for n in names)
    assert any(n.endswith('p.jpg') for n in names)


def test_backups_can_be_downloaded(client, app, db, admin_login):
    client.post('/admin/maintenance/backup', data={'csrf_token': CSRF})
    with app.app_context():
        name = maintenance.list_backups()[0]['name']

    response = client.get(f'/admin/maintenance/backup/{name}/download')
    assert response.status_code == 200
    assert name in response.headers['Content-Disposition']


def test_backup_names_are_validated(client, db, admin_login):
    """The name comes from the URL, so it must never be joined blindly."""
    assert maintenance.is_backup_name('../../etc/passwd') is False
    assert maintenance.is_backup_name('home-cmms-backup-20260101-000000.tar.gz') is True
    assert client.get('/admin/maintenance/backup/..%2F..%2Fetc%2Fpasswd/download').status_code == 404


def test_backups_can_be_deleted(client, app, db, admin_login):
    client.post('/admin/maintenance/backup', data={'csrf_token': CSRF})
    with app.app_context():
        name = maintenance.list_backups()[0]['name']

    client.post(f'/admin/maintenance/backup/{name}/delete', data={'csrf_token': CSRF})
    with app.app_context():
        assert maintenance.list_backups() == []


def test_retention_prunes_older_backups(client, app, db, admin_login):
    with app.app_context():
        for i in range(5):
            open(os.path.join(maintenance.backup_dir(),
                              f'home-cmms-backup-2026010{i}-000000.tar.gz'), 'wb').close()
        assert len(maintenance.list_backups()) == 5
        removed = maintenance.prune_backups(keep=2)
        assert removed == 3
        assert len(maintenance.list_backups()) == 2


# ── storage integrity ──────────────────────────────────────────────────────

def test_scan_is_clean_when_nothing_has_drifted(client, app, db, admin_login):
    asset = create_asset(name='Furnace')
    upload(client, asset)
    with app.app_context():
        assert maintenance.scan_storage()['clean'] is True


def test_scan_finds_a_record_whose_file_vanished(client, app, db, admin_login):
    asset = create_asset(name='Furnace')
    upload(client, asset)
    att = Attachment.query.one()

    with app.app_context():
        from app.utils import entity_upload_dir
        os.remove(os.path.join(entity_upload_dir('asset', asset.id), att.stored_filename))
        report = maintenance.scan_storage()
    assert len(report['missing_files']) == 1
    assert report['clean'] is False


def test_scan_finds_a_file_nothing_references(client, app, db, admin_login):
    asset = create_asset(name='Furnace')
    upload(client, asset)

    with app.app_context():
        from app.utils import entity_upload_dir
        stray = os.path.join(entity_upload_dir('asset', asset.id), 'left-behind.pdf')
        with open(stray, 'wb') as fh:
            fh.write(b'orphan')
        report = maintenance.scan_storage()
    assert len(report['stray_files']) == 1


def test_scan_finds_a_stale_thumbnail(client, app, db, admin_login):
    with app.app_context():
        from app.utils import thumbnail_dir
        os.makedirs(thumbnail_dir(), exist_ok=True)
        open(os.path.join(thumbnail_dir(), '999999.jpg'), 'wb').close()
        report = maintenance.scan_storage()
    assert '999999.jpg' in report['stray_thumbnails']


def test_cleanup_removes_exactly_what_the_scan_reported(client, app, db, admin_login):
    asset = create_asset(name='Furnace')
    upload(client, asset, 'keeper.pdf')
    keeper = Attachment.query.one()

    with app.app_context():
        from app.utils import entity_upload_dir, thumbnail_dir
        stray = os.path.join(entity_upload_dir('asset', asset.id), 'left-behind.pdf')
        with open(stray, 'wb') as fh:
            fh.write(b'orphan')
        os.makedirs(thumbnail_dir(), exist_ok=True)
        open(os.path.join(thumbnail_dir(), '999999.jpg'), 'wb').close()

    client.post('/admin/maintenance/clean-storage', data={'csrf_token': CSRF})

    with app.app_context():
        report = maintenance.scan_storage()
        assert report['clean'] is True
        assert not os.path.exists(stray)
    # the healthy attachment is untouched
    assert Attachment.query.count() == 1
    assert Attachment.query.one().id == keeper.id


def test_cleanup_is_safe_when_there_is_nothing_to_do(client, app, db, admin_login):
    asset = create_asset(name='Furnace')
    upload(client, asset)
    client.post('/admin/maintenance/clean-storage', data={'csrf_token': CSRF})
    assert Attachment.query.count() == 1


# ── database ───────────────────────────────────────────────────────────────

def test_integrity_check_passes(app, db, user):
    with app.app_context():
        result = maintenance.check_database()
    assert result['ok'] is True
    assert result['integrity'] == ['ok']


def test_vacuum_reports_sizes(client, app, db, admin_login):
    create_work_order(title='Filler')
    with app.app_context():
        result = maintenance.vacuum_database()
    assert result['before'] > 0
    assert result['after'] > 0


def test_vacuum_through_the_page(client, db, admin_login):
    response = client.post('/admin/maintenance/vacuum', data={'csrf_token': CSRF},
                           follow_redirects=True)
    assert 'Reclaimed' in response.get_data(as_text=True)


def test_wal_checkpoint(client, db, admin_login):
    response = client.post('/admin/maintenance/checkpoint', data={'csrf_token': CSRF},
                           follow_redirects=True)
    assert 'write-ahead log' in response.get_data(as_text=True)


def test_clearing_the_thumbnail_cache(client, app, db, admin_login):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (200, 200)).save(buf, 'JPEG')
    asset = create_asset(name='Furnace')
    client.post(f'/assets/{asset.id}/attachments',
                data={'file': (io.BytesIO(buf.getvalue()), 'p.jpg'), 'csrf_token': CSRF},
                content_type='multipart/form-data')
    att = Attachment.query.one()
    client.get(f'/attachments/{att.id}/thumbnail')

    with app.app_context():
        from app.utils import thumbnail_path
        assert os.path.exists(thumbnail_path(att.id))

    client.post('/admin/maintenance/clear-thumbnails', data={'csrf_token': CSRF})

    with app.app_context():
        from app.utils import thumbnail_path
        assert not os.path.exists(thumbnail_path(att.id))
    # and it rebuilds on demand
    assert client.get(f'/attachments/{att.id}/thumbnail').status_code == 200


def test_sizes_read_sensibly_at_disk_scale():
    """The status panel reports disk capacity, so MB is not enough."""
    from app.utils import format_file_size
    assert format_file_size(900) == '900 B'
    assert format_file_size(51200) == '50.0 KB'
    assert format_file_size(54525952) == '52.0 MB'
    assert format_file_size(536870912000) == '500.0 GB'
    assert format_file_size(2199023255552) == '2.0 TB'


# ── in-place actions ───────────────────────────────────────────────────────

ASYNC = {'X-Requested-With': 'fetch'}


def test_a_fetch_gets_the_rerendered_page_not_a_redirect(client, db, admin_login):
    """So the browser can swap it in rather than navigating and losing scroll."""
    response = client.post('/admin/maintenance/check-database',
                           data={'csrf_token': CSRF}, headers=ASYNC)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="maintenance"' in body
    assert 'Database integrity check passed' in body      # the flash came along


def test_a_plain_post_still_redirects(client, db, admin_login):
    """Without JS the forms must behave like ordinary forms."""
    response = client.post('/admin/maintenance/check-database', data={'csrf_token': CSRF})
    assert response.status_code == 302
    assert '/admin/maintenance' in response.headers['Location']


def test_every_action_supports_both_modes(client, app, db, admin_login):
    actions = ['/admin/maintenance/check-database', '/admin/maintenance/vacuum',
               '/admin/maintenance/checkpoint', '/admin/maintenance/clear-thumbnails',
               '/admin/maintenance/clean-storage', '/admin/maintenance/run-pm-check',
               '/admin/maintenance/backup']
    for path in actions:
        plain = client.post(path, data={'csrf_token': CSRF})
        assert plain.status_code == 302, path
        fetched = client.post(path, data={'csrf_token': CSRF}, headers=ASYNC)
        assert fetched.status_code == 200, path
        assert 'id="maintenance"' in fetched.get_data(as_text=True), path


def test_scan_results_come_back_in_the_fetched_markup(client, app, db, admin_login):
    asset = create_asset(name='Furnace')
    upload(client, asset)
    with app.app_context():
        from app.utils import entity_upload_dir
        with open(os.path.join(entity_upload_dir('asset', asset.id), 'stray.pdf'), 'wb') as fh:
            fh.write(b'orphan')

    response = client.get('/admin/maintenance?scan=1', headers=ASYNC)
    body = response.get_data(as_text=True)
    assert 'stray.pdf' in body
    assert 'Delete orphaned data' in body


def test_cleanup_via_fetch_returns_a_fresh_scan(client, app, db, admin_login):
    """The result should show the cleaned state, not a stale one."""
    asset = create_asset(name='Furnace')
    upload(client, asset)
    with app.app_context():
        from app.utils import entity_upload_dir
        with open(os.path.join(entity_upload_dir('asset', asset.id), 'stray.pdf'), 'wb') as fh:
            fh.write(b'orphan')

    response = client.post('/admin/maintenance/clean-storage',
                           data={'csrf_token': CSRF}, headers=ASYNC)
    body = response.get_data(as_text=True)
    assert 'Everything checks out' in body
    assert 'stray.pdf' not in body


def test_csrf_is_still_required_for_fetch_actions(client, db, admin_login):
    assert client.post('/admin/maintenance/vacuum', data={}, headers=ASYNC).status_code == 403


def test_the_page_marks_its_actions_for_enhancement(client, db, admin_login):
    body = client.get('/admin/maintenance').get_data(as_text=True)
    assert 'data-async' in body
    assert 'data-confirm' in body
    assert 'id="maintenance"' in body
    # forms keep a real action and method, so they work with JS disabled
    assert 'method="post"' in body


def test_downloads_are_not_intercepted(client, app, db, admin_login):
    """A download must stay a real navigation, not a fetch-and-swap."""
    client.post('/admin/maintenance/backup', data={'csrf_token': CSRF})
    body = client.get('/admin/maintenance').get_data(as_text=True)
    download_link = [line for line in body.splitlines() if 'download_backup' in line or '/download' in line]
    assert download_link
    assert not any('data-async' in line for line in download_link)


# ── token UI ───────────────────────────────────────────────────────────────

def test_generating_a_token_requires_a_name(client, db, admin_login):
    from app.models.api_token import ApiToken
    from app.models.user import User

    target = User.query.filter_by(username='boss').one()
    client.post(f'/admin/users/{target.id}/api-token',
                data={'csrf_token': CSRF, 'token_name': ''})
    assert ApiToken.query.count() == 0


def test_a_named_token_is_shown_once_in_its_own_field(client, db, admin_login):
    from app.models.api_token import ApiToken
    from app.models.user import User

    target = User.query.filter_by(username='boss').one()
    response = client.post(f'/admin/users/{target.id}/api-token',
                           data={'csrf_token': CSRF, 'token_name': 'Home Assistant'},
                           follow_redirects=True)
    body = response.get_data(as_text=True)

    token = ApiToken.query.one()
    assert token.name == 'Home Assistant'
    # The value sits alone in an input, not embedded in a sentence.
    assert 'class="token-value" readonly value=' in body
    assert 'data-copy-target' in body
    assert 'data-no-dismiss' in body      # never auto-hidden while being copied


def test_tokens_are_listed_with_their_names(client, db, admin_login):
    from app.models.api_token import ApiToken
    from app.models.user import User

    target = User.query.filter_by(username='boss').one()
    ApiToken.issue(target, 'Home Assistant')
    db.session.commit()

    body = client.get(f'/admin/users/{target.id}/edit').get_data(as_text=True)
    assert 'Home Assistant' in body
    assert 'Revoke' in body


def test_revoking_one_token(client, db, admin_login):
    from app.models.api_token import ApiToken
    from app.models.user import User

    target = User.query.filter_by(username='boss').one()
    keep, _ = ApiToken.issue(target, 'Keep')
    drop, _ = ApiToken.issue(target, 'Drop')
    db.session.commit()

    client.post(f'/admin/users/{target.id}/api-token/{drop.id}/revoke', data={'csrf_token': CSRF})
    remaining = [t.name for t in ApiToken.query.all()]
    assert remaining == ['Keep']


def test_a_token_belonging_to_someone_else_cannot_be_revoked(client, db, admin_login):
    from app.models.api_token import ApiToken
    from app.models.user import User

    other = make_user('someone')
    theirs, _ = ApiToken.issue(other, 'Theirs')
    db.session.commit()
    boss = User.query.filter_by(username='boss').one()

    client.post(f'/admin/users/{boss.id}/api-token/{theirs.id}/revoke', data={'csrf_token': CSRF})
    assert ApiToken.query.count() == 1      # untouched
