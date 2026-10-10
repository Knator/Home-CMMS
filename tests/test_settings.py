"""In-app configuration, on two pages separate from Maintenance.

Preferences (admins) is how work orders and PMs behave. Settings (super admins)
is server performance and security — what the server accepts and what it
reaches out to. Maintenance is about keeping the instance running. All of it
lives in the database rather than .env, because .env is a deployment artifact
the operator owns — often mounted read-only, and needing a restart to take
effect.
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
    """An admin: Preferences, not Settings."""
    login('admin')
    prime_csrf(client)
    return client


@pytest.fixture
def owner_client(client, db, login):
    """A super admin: Settings as well."""
    make_user('owner', role='super_admin')
    login('owner')
    prime_csrf(client)
    return client


@pytest.fixture
def archived(db):
    wo = create_work_order(title='Done', wo_type='planned', status='completed')
    archive_work_order(wo)
    return wo


# ── the pages ──────────────────────────────────────────────────────────────

def test_a_user_gets_neither(client, db, user, login):
    login()
    assert client.get('/admin/preferences').status_code == 302
    assert client.get('/admin/settings').status_code == 302


def test_an_admin_gets_preferences_but_not_settings(admin_client):
    assert admin_client.get('/admin/preferences').status_code == 200
    response = admin_client.get('/admin/settings')
    assert response.status_code == 302 and response.headers['Location'] == '/'


def test_a_refused_save_changes_nothing(admin_client, app):
    """Refused before anything runs, not merely redirected afterwards."""
    admin_client.post('/admin/settings', data={'csrf_token': CSRF})   # all unticked
    with app.test_request_context():
        assert app_settings.get('update_check_enabled') is True
        assert app_settings.get('upload_limit_enabled') is True
    assert Setting.query.count() == 0


def test_a_super_admin_gets_both(owner_client):
    assert owner_client.get('/admin/preferences').status_code == 200
    assert owner_client.get('/admin/settings').status_code == 200


def card_titles(body):
    import re
    return re.findall(r'<span class="card-title">([^<]+)</span>', body)


def test_each_page_holds_its_own_cards(owner_client):
    assert card_titles(owner_client.get('/admin/preferences').get_data(as_text=True)) \
        == ['Work Orders', 'Preventive Maintenance']
    assert card_titles(owner_client.get('/admin/settings').get_data(as_text=True)) \
        == ['Updates', 'Attachments']


def test_it_is_separate_from_maintenance(owner_client):
    """Two pages, two jobs. Maintenance is for super admins."""
    settings = owner_client.get('/admin/settings').get_data(as_text=True)
    maintenance = owner_client.get('/admin/maintenance').get_data(as_text=True)
    assert '/admin/maintenance' in settings
    # Maintenance keeps its own concerns; settings does not duplicate them.
    assert 'Backups' in maintenance and 'Backups' not in settings


# ── neither page touches the other's ──────────────────────────────────────

def test_saving_settings_leaves_preferences_alone(owner_client, app):
    """An unticked checkbox is absent from the form, so a handler that wrote
    every key would switch off whatever the other page owns."""
    owner_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '250',
    })
    with app.test_request_context():
        assert app_settings.get('max_upload_mb') == 250
        assert app_settings.get('update_check_enabled') is False      # unticked here
        assert app_settings.get('allow_archived_deletion') is True    # not this page's
        assert app_settings.get('pm_stall_on_open') is True
        assert app_settings.get('default_grace_days') == 10


def test_saving_preferences_leaves_settings_alone(admin_client, app):
    admin_client.post('/admin/preferences', data={
        'csrf_token': CSRF, 'default_grace_days': '3',
    })
    with app.test_request_context():
        assert app_settings.get('default_grace_days') == 3
        assert app_settings.get('allow_archived_deletion') is False   # unticked here
        assert app_settings.get('update_check_enabled') is True       # not this page's
        assert app_settings.get('upload_limit_enabled') is True


# ── defaults ───────────────────────────────────────────────────────────────

def test_defaults_apply_with_nothing_stored(app):
    with app.test_request_context():
        assert app_settings.get('allow_archived_deletion') is True
        assert app_settings.get('upload_limit_enabled') is True
        assert app_settings.get('max_upload_mb') == 100
        assert Setting.query.count() == 0


def test_an_unparseable_size_is_refused_rather_than_stored(owner_client, app):
    response = owner_client.post('/admin/settings', data={
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
    # Unticked: the form posts nothing for it.
    admin_client.post('/admin/preferences', data={'csrf_token': CSRF})
    wo_id = archived.id
    admin_client.post(f'/work-orders/{wo_id}/delete', data={'csrf_token': CSRF})
    assert _db.session.get(WorkOrder, wo_id) is not None


def test_the_button_goes_away_too(admin_client, db, archived):
    # Unticked: the form posts nothing for it.
    admin_client.post('/admin/preferences', data={'csrf_token': CSRF})
    html = admin_client.get(f'/work-orders/{archived.id}').get_data(as_text=True)
    assert 'Delete Work Order' not in html


def test_live_work_orders_are_unaffected(admin_client, db):
    # Unticked: the form posts nothing for it.
    admin_client.post('/admin/preferences', data={'csrf_token': CSRF})
    wo = create_work_order(title='Still going', wo_type='unplanned')
    wo_id = wo.id
    admin_client.post(f'/work-orders/{wo_id}/delete', data={'csrf_token': CSRF})
    assert _db.session.get(WorkOrder, wo_id) is None


# ── the upload limit ───────────────────────────────────────────────────────

def upload(client, wo_id, size):
    return client.post(f'/work-orders/{wo_id}/attachments', data={
        'csrf_token': CSRF, 'file': (io.BytesIO(b'x' * size), 'clip.mp4'),
    }, content_type='multipart/form-data', follow_redirects=True)


def test_the_stored_limit_is_what_gets_enforced(owner_client, db, app):
    """The point of storing it: no restart, no .env edit."""
    from app.models.attachment import Attachment
    wo = create_work_order(title='Noise', wo_type='unplanned')

    owner_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '1',
    })
    upload(owner_client, wo.id, 2 * 1024 * 1024)
    assert Attachment.query.count() == 0

    owner_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '50',
    })
    upload(owner_client, wo.id, 2 * 1024 * 1024)
    assert Attachment.query.count() == 1


def test_switching_the_limit_off_accepts_anything(owner_client, db):
    from app.models.attachment import Attachment
    wo = create_work_order(title='Noise', wo_type='unplanned')
    owner_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'max_upload_mb': '1',        # limit box unchecked
    })
    upload(owner_client, wo.id, 3 * 1024 * 1024)
    assert Attachment.query.count() == 1


def test_the_error_message_quotes_the_limit_in_force(owner_client, db):
    wo = create_work_order(title='Noise', wo_type='unplanned')
    owner_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '2',
    })
    response = upload(owner_client, wo.id, 4 * 1024 * 1024)
    assert b'2 MB' in response.data


# ── the environment still wins ─────────────────────────────────────────────

def test_an_env_var_overrides_the_stored_value_and_locks_the_field(owner_client, app):
    """Setting a variable is a deliberate act by whoever runs the server; a web
    form should not quietly override it."""
    app.config['ENV_SETTING_OVERRIDES'] = {'MAX_UPLOAD_MB': '7'}
    owner_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '500',
    })
    with app.test_request_context():
        assert app_settings.get('max_upload_mb') == 7      # not 500

    html = owner_client.get('/admin/settings').get_data(as_text=True)
    assert 'disabled' in html
    assert 'Set by the environment' in html


def test_without_the_env_var_the_field_is_editable(owner_client, app):
    app.config['ENV_SETTING_OVERRIDES'] = {}
    html = owner_client.get('/admin/settings').get_data(as_text=True)
    assert 'Set by the environment' not in html


def test_a_blank_grace_is_refused_rather_than_stored(admin_client, app):
    """Cleared on purpose is a mistake worth reporting; absent is not. The
    difference is what keeps one field from making the page unsaveable."""
    admin_client.post('/admin/preferences', data={
        'csrf_token': CSRF, 'default_grace_days': '',
    }, follow_redirects=True)
    with app.test_request_context():
        assert app_settings.get('default_grace_days') == 10   # unchanged


def test_omitting_the_grace_leaves_it_alone(admin_client, app):
    """A POST that does not carry the field must still save everything else."""
    admin_client.post('/admin/preferences', data={
        'csrf_token': CSRF, 'default_grace_days': '4',
    }, follow_redirects=True)
    admin_client.post('/admin/preferences', data={
        'csrf_token': CSRF, 'auto_archive_days': '45',
    }, follow_redirects=True)
    with app.test_request_context():
        assert app_settings.get('default_grace_days') == 4     # untouched
        assert app_settings.get('auto_archive_days') == 45     # and the rest saved
