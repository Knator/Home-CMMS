"""In-app preferences: a settings page separate from Maintenance.

Maintenance is about keeping the instance running. Settings is about how the
application behaves. Preferences live in the database rather than .env, because
.env is a deployment artifact the operator owns — often mounted read-only, and
needing a restart to take effect.
"""
import io

import pytest

from app import settings as app_settings
from app.extensions import db as _db
from app.models.setting import Setting
from app.models.work_order import WorkOrder
from app.services import archive_work_order, create_work_order
from tests.conftest import CSRF, make_user, prime_csrf


@pytest.fixture
def admin_client(client, db, admin, login):
    login('admin')
    prime_csrf(client)
    return client


@pytest.fixture
def archived(db):
    wo = create_work_order(title='Done', wo_type='planned', status='completed')
    archive_work_order(wo)
    return wo


# ── the page ───────────────────────────────────────────────────────────────

def test_settings_is_admin_only(client, db, user, login):
    login()
    assert client.get('/admin/settings').status_code in (302, 403)


def test_an_admin_can_open_it(admin_client):
    response = admin_client.get('/admin/settings')
    assert response.status_code == 200
    assert b'Settings' in response.data


def test_it_is_separate_from_maintenance(admin_client):
    """Two pages, two jobs — and each links to the other."""
    settings = admin_client.get('/admin/settings').get_data(as_text=True)
    maintenance = admin_client.get('/admin/maintenance').get_data(as_text=True)
    assert '/admin/maintenance' in settings
    # Maintenance keeps its own concerns; settings does not duplicate them.
    assert 'Backups' in maintenance and 'Backups' not in settings


# ── defaults ───────────────────────────────────────────────────────────────

def test_defaults_apply_with_nothing_stored(app):
    with app.test_request_context():
        assert app_settings.get('allow_archived_deletion') is True
        assert app_settings.get('upload_limit_enabled') is True
        assert app_settings.get('max_upload_mb') == 100
        assert Setting.query.count() == 0


def test_saving_stores_only_what_changed(admin_client, app):
    admin_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '250',
    })
    with app.test_request_context():
        assert app_settings.get('allow_archived_deletion') is False   # unchecked
        assert app_settings.get('max_upload_mb') == 250


def test_an_unparseable_size_is_refused_rather_than_stored(admin_client, app):
    response = admin_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': 'big',
    })
    assert b'at least 1 MB' in response.data
    with app.test_request_context():
        assert app_settings.get('max_upload_mb') == 100


# ── archived deletion ──────────────────────────────────────────────────────

def test_archived_work_orders_are_deletable_by_default(admin_client, db, archived):
    wo_id = archived.id
    admin_client.post(f'/work-orders/{wo_id}/delete', data={'csrf_token': CSRF})
    assert _db.session.get(WorkOrder, wo_id) is None


def test_switching_it_off_stops_deletion(admin_client, db, archived):
    admin_client.post('/admin/settings', data={'csrf_token': CSRF,
                                               'upload_limit_enabled': '1',
                                               'max_upload_mb': '100'})
    wo_id = archived.id
    admin_client.post(f'/work-orders/{wo_id}/delete', data={'csrf_token': CSRF})
    assert _db.session.get(WorkOrder, wo_id) is not None


def test_the_button_goes_away_too(admin_client, db, archived):
    admin_client.post('/admin/settings', data={'csrf_token': CSRF,
                                               'upload_limit_enabled': '1',
                                               'max_upload_mb': '100'})
    html = admin_client.get(f'/work-orders/{archived.id}').get_data(as_text=True)
    assert 'Delete Work Order' not in html


def test_live_work_orders_are_unaffected(admin_client, db):
    admin_client.post('/admin/settings', data={'csrf_token': CSRF,
                                               'upload_limit_enabled': '1',
                                               'max_upload_mb': '100'})
    wo = create_work_order(title='Still going', wo_type='unplanned')
    wo_id = wo.id
    admin_client.post(f'/work-orders/{wo_id}/delete', data={'csrf_token': CSRF})
    assert _db.session.get(WorkOrder, wo_id) is None


# ── the upload limit ───────────────────────────────────────────────────────

def upload(client, wo_id, size):
    return client.post(f'/work-orders/{wo_id}/attachments', data={
        'csrf_token': CSRF, 'file': (io.BytesIO(b'x' * size), 'clip.mp4'),
    }, content_type='multipart/form-data', follow_redirects=True)


def test_the_stored_limit_is_what_gets_enforced(admin_client, db, app):
    """The point of storing it: no restart, no .env edit."""
    from app.models.attachment import Attachment
    wo = create_work_order(title='Noise', wo_type='unplanned')

    admin_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '1',
    })
    upload(admin_client, wo.id, 2 * 1024 * 1024)
    assert Attachment.query.count() == 0

    admin_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '50',
    })
    upload(admin_client, wo.id, 2 * 1024 * 1024)
    assert Attachment.query.count() == 1


def test_switching_the_limit_off_accepts_anything(admin_client, db):
    from app.models.attachment import Attachment
    wo = create_work_order(title='Noise', wo_type='unplanned')
    admin_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'max_upload_mb': '1',        # limit box unchecked
    })
    upload(admin_client, wo.id, 3 * 1024 * 1024)
    assert Attachment.query.count() == 1


def test_the_error_message_quotes_the_limit_in_force(admin_client, db):
    wo = create_work_order(title='Noise', wo_type='unplanned')
    admin_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '2',
    })
    response = upload(admin_client, wo.id, 4 * 1024 * 1024)
    assert b'2 MB' in response.data


# ── the environment still wins ─────────────────────────────────────────────

def test_an_env_var_overrides_the_stored_value_and_locks_the_field(admin_client, app):
    """Setting a variable is a deliberate act by whoever runs the server; a web
    form should not quietly override it."""
    app.config['ENV_SETTING_OVERRIDES'] = {'MAX_UPLOAD_MB': '7'}
    admin_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '500',
    })
    with app.test_request_context():
        assert app_settings.get('max_upload_mb') == 7      # not 500

    html = admin_client.get('/admin/settings').get_data(as_text=True)
    assert 'disabled' in html
    assert 'Set by the environment' in html


def test_without_the_env_var_the_field_is_editable(admin_client, app):
    app.config['ENV_SETTING_OVERRIDES'] = {}
    html = admin_client.get('/admin/settings').get_data(as_text=True)
    assert 'Set by the environment' not in html
