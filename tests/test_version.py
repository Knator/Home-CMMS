"""The release version, and the update check.

The version is a constant because `.dockerignore` excludes `.git/`, so there is
no tag to read inside the image. The update check is the only outbound network
call in the application, which shapes everything about how it is written.
"""
import pathlib
import re
import subprocess
import urllib.error
from datetime import timedelta
from unittest.mock import patch

import pytest

from app import updates
from app.extensions import db as _db
from app.models.setting import Setting
from app.utils import utcnow
from app.version import __version__
from tests.conftest import CSRF, make_user, prime_csrf

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def admin_client(client, db, login):
    make_user('admin', role='admin', password='Password123!')
    login('admin', 'Password123!')
    prime_csrf(client)
    return client


@pytest.fixture(autouse=True)
def clear_cache(app):
    """Each test starts with nothing remembered from the last."""
    with app.app_context():
        for key in ('_update_latest_version', '_update_checked_at', '_update_error'):
            row = _db.session.get(Setting, key)
            if row:
                _db.session.delete(row)
        _db.session.commit()


# ── the version itself ─────────────────────────────────────────────────────

@pytest.mark.precheck
def test_the_version_looks_like_a_version():
    parts = updates.version_parts(__version__)
    assert len(parts) >= 2, __version__


@pytest.mark.precheck
def test_the_constant_matches_the_newest_git_tag():
    """A forgotten bump ships a version that lies about itself. Skipped where
    there is no git checkout — inside the image, for instance."""
    if not (ROOT / '.git').exists():
        pytest.skip('no git checkout')
    try:
        tag = subprocess.run(
            ['git', 'describe', '--tags', '--abbrev=0', '--match', 'v[0-9]*'],
            cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pytest.skip('git unavailable')
    if not tag:
        pytest.skip('no version tags yet')
    assert updates.version_parts(tag) == updates.version_parts(__version__), (
        f'app/version.py says {__version__} but the newest tag is {tag}')


def test_the_maintenance_page_shows_it(admin_client):
    html = admin_client.get('/admin/maintenance').get_data(as_text=True)
    assert 'Release Version' in html
    assert __version__ in html


# ── comparing versions ─────────────────────────────────────────────────────

@pytest.mark.parametrize('running,latest,expected', [
    ('1.0.0', '1.0.0', 'current'),
    ('1.0.0', 'v1.0.0', 'current'),      # the tag's v is not significant
    ('1.0.0', '1.0.1', 'behind'),
    ('1.0.0', '2.0.0', 'behind'),
    ('1.0.1', '1.0.0', 'ahead'),
    ('1.10.0', '1.9.0', 'ahead'),        # numeric, not lexicographic
    ('1.0.1-dev', '1.0.1', 'behind'),    # a pre-release is not the release
    ('1.0.0', '', 'current'),            # nothing known: do not cry wolf
    ('1.0.0', 'garbage', 'current'),
])
def test_version_comparison(running, latest, expected):
    assert updates.compare(running, latest) == expected


# ── the check ──────────────────────────────────────────────────────────────

def test_it_reports_being_up_to_date(app, admin_client):
    with patch('app.updates._fetch_latest', return_value=f'v{__version__}'):
        html = admin_client.get('/admin/maintenance').get_data(as_text=True)
    assert 'up to date' in html


def test_it_reports_a_newer_release(app, admin_client):
    with patch('app.updates._fetch_latest', return_value='v99.0.0'):
        html = admin_client.get('/admin/maintenance').get_data(as_text=True)
    assert 'update available' in html
    assert '99.0.0' in html


def test_an_offline_instance_still_renders_the_page(app, admin_client):
    """The page must not hang or error because GitHub is unreachable."""
    with patch('app.updates._fetch_latest',
               side_effect=urllib.error.URLError('no route to host')):
        response = admin_client.get('/admin/maintenance')
    assert response.status_code == 200
    assert b'Could not check for updates' in response.data


@pytest.mark.parametrize('failure', [
    urllib.error.URLError('dns'), TimeoutError('slow'), ValueError('not json'),
    OSError('refused'),
])
def test_every_kind_of_failure_is_swallowed(app, admin_client, failure):
    with patch('app.updates._fetch_latest', side_effect=failure):
        assert admin_client.get('/admin/maintenance').status_code == 200


# ── it does not call out more than it needs to ─────────────────────────────

def test_the_result_is_cached(app, admin_client):
    with patch('app.updates._fetch_latest', return_value='v1.0.0') as fetch:
        admin_client.get('/admin/maintenance')
        admin_client.get('/admin/maintenance')
        admin_client.get('/admin/maintenance')
    assert fetch.call_count == 1, 'called GitHub on every page load'


def test_the_cache_lapses(app, admin_client):
    with patch('app.updates._fetch_latest', return_value='v1.0.0') as fetch:
        admin_client.get('/admin/maintenance')
        with app.test_request_context():
            stale = (utcnow() - updates.CACHE_FOR - timedelta(minutes=1)).isoformat()
            updates._put('_update_checked_at', stale)
            _db.session.commit()
        admin_client.get('/admin/maintenance')
    assert fetch.call_count == 2


def test_check_now_ignores_the_cache(app, admin_client):
    with patch('app.updates._fetch_latest', return_value='v1.0.0') as fetch:
        admin_client.get('/admin/maintenance')
        admin_client.post('/admin/maintenance/check-updates',
                          data={'csrf_token': CSRF})
    assert fetch.call_count == 2


def test_nothing_is_sent_about_the_instance(app):
    """An unauthenticated GET of a public listing; GitHub sees an address and a
    user agent, and no request body at all."""
    import inspect
    source = inspect.getsource(updates._fetch_latest)
    assert 'data=' not in source
    assert 'urlopen' in source
    assert updates.RELEASES_URL.startswith('https://api.github.com/')


def test_the_url_is_a_constant(app):
    """Nothing user-supplied reaches it, so this adds no SSRF surface."""
    import inspect
    source = inspect.getsource(updates._fetch_latest)
    assert 'RELEASES_URL' in source
    assert 'request.args' not in source


# ── it can be switched off ─────────────────────────────────────────────────

def test_switching_it_off_stops_the_call(app, admin_client):
    admin_client.post('/admin/settings', data={
        'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '100',
        'allow_archived_deletion': '1', 'auto_archive_days': '90',
        # update_check_enabled omitted -> off
    })
    with patch('app.updates._fetch_latest', return_value='v99.0.0') as fetch:
        html = admin_client.get('/admin/maintenance').get_data(as_text=True)
    assert fetch.call_count == 0
    assert 'Update checking is off' in html
    assert 'update available' not in html


def test_it_is_on_by_default(app):
    from app import settings as app_settings
    with app.test_request_context():
        assert app_settings.get('update_check_enabled') is True


def test_check_now_is_admin_only(client, db, user, login):
    login()
    prime_csrf(client)
    assert client.post('/admin/maintenance/check-updates',
                       data={'csrf_token': CSRF}).status_code in (302, 403)


# ── the release check ──────────────────────────────────────────────────────
#
# Releasing is manual. The only thing automated is the refusal to build an image
# whose version constant disagrees with the release tag, which is the last
# moment a forgotten bump is still free to fix.

def workflow(name):
    import yaml
    return yaml.safe_load((ROOT / '.github' / 'workflows' / name).read_text())


@pytest.mark.precheck
def test_nothing_tags_or_releases_on_its_own():
    """Releasing is a deliberate manual act, from whichever branch it belongs
    on. No workflow may create a tag, a release, or a version bump."""
    for path in (ROOT / '.github' / 'workflows').glob('*.yml'):
        body = path.read_text()
        assert 'git tag' not in body, path.name
        assert 'git push' not in body, path.name
        assert 'gh release create' not in body, path.name


@pytest.mark.precheck
def test_the_release_tag_is_checked_against_the_constant():
    verify = workflow('docker-image.yml')['jobs']['verify']
    script = ' '.join(s.get('run', '') for s in verify['steps'])
    assert 'release.tag_name' in str(verify['steps'])
    assert '__version__' in script


@pytest.mark.precheck
def test_the_check_only_runs_for_a_release():
    """A workflow_dispatch rebuild has no release tag to compare against."""
    assert workflow('docker-image.yml')['jobs']['verify']['if'] == \
        "github.event_name == 'release'"


@pytest.mark.precheck
def test_a_mismatched_version_stops_the_image_being_built():
    """Reporting the mismatch is not enough: an image that misreports its own
    version is only a number on a page, so it would go unnoticed."""
    publish = workflow('docker-image.yml')['jobs']['publish']
    assert publish['needs'] == 'verify'
    assert "needs.verify.result != 'failure'" in publish['if']


@pytest.mark.precheck
def test_a_skipped_check_still_builds():
    """`verify` is skipped on a manual rebuild, and a skipped dependency would
    skip the build with it unless `always()` is there."""
    assert 'always()' in workflow('docker-image.yml')['jobs']['publish']['if']
