"""Saving preferences while one of them is governed by the environment.

A setting with its environment variable set renders disabled, so the browser
does not submit it. The save path therefore has to treat it as absent rather
than as missing input — otherwise the whole page cannot be saved, taking every
unrelated preference on it along too.
"""
import pytest

from tests.conftest import CSRF, make_user, prime_csrf


@pytest.fixture
def admin_client(app, client, db, login):
    make_user('admin', role='admin', password='Password123!')
    login('admin', 'Password123!')
    prime_csrf(client)
    return client


@pytest.fixture
def locked(app):
    """MAX_UPLOAD_MB set in the environment, as .env.docker.example invites."""
    app.config['ENV_SETTING_OVERRIDES'] = {'MAX_UPLOAD_MB': '100'}
    return app


def form(**extra):
    """The page as the browser posts it: the locked field is disabled, so it is
    simply not there."""
    data = {'csrf_token': CSRF, 'upload_limit_enabled': '1',
            'auto_archive_days': '90'}
    data.update(extra)
    return data


def test_the_page_can_be_saved_at_all(locked, admin_client):
    response = admin_client.post('/admin/settings', data=form(),
                                 follow_redirects=True)
    body = response.get_data(as_text=True)
    assert 'Enter a maximum attachment size' not in body, (
        'the locked field is reported as missing, so the page cannot be saved'
    )
    assert 'Settings saved' in body


def test_an_unrelated_preference_still_saves(locked, admin_client):
    """The real cost of the bug: one locked field blocked everything else."""
    admin_client.post('/admin/settings',
                      data=form(pm_stall_on_open='1'), follow_redirects=True)
    from app import settings as app_settings
    assert app_settings.get('pm_stall_on_open') is True

    admin_client.post('/admin/settings', data=form(), follow_redirects=True)
    assert app_settings.get('pm_stall_on_open') is False


def test_the_environment_still_governs_the_locked_value(locked, admin_client):
    """Saving must not quietly store a value that the environment overrides."""
    from app import settings as app_settings
    admin_client.post('/admin/settings', data=form(max_upload_mb='7'),
                      follow_redirects=True)
    assert app_settings.get('max_upload_mb') == 100, (
        'a posted value overrode the environment, which is what locking prevents'
    )


def test_an_unlocked_field_is_still_required(admin_client):
    """The original check has to survive: with no environment variable, leaving
    the box empty while the limit is on is still a mistake worth reporting."""
    response = admin_client.post('/admin/settings',
                                 data=form(max_upload_mb=''),
                                 follow_redirects=True)
    assert 'Enter a maximum attachment size' in response.get_data(as_text=True)
