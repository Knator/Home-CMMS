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

def test_the_version_looks_like_a_version():
    parts = updates.version_parts(__version__)
    assert len(parts) >= 2, __version__


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


# ── the release workflow ───────────────────────────────────────────────────
#
# The bump is manual, so the workflow's job is to refuse a release that does not
# match it: tagging while app/version.py says something else would leave the
# tagged source disagreeing with the release, which is what
# test_the_constant_matches_the_newest_git_tag catches on the very commit people
# download. It deliberately does not write to the repository — `master` is
# protected by a ruleset, and a ruleset bypass can only name installed GitHub
# Apps, which github-actions[bot] is not.

def workflow(name):
    import yaml
    return yaml.safe_load((ROOT / '.github' / 'workflows' / name).read_text())


def release_steps():
    return [s.get('name') or s.get('uses')
            for s in workflow('release.yml')['jobs']['release']['steps']]


VERSION_CHECK = 'Check app/version.py already says that'


def test_the_version_is_checked_before_the_tests():
    """A forgotten bump should cost seconds, not the whole suite."""
    steps = release_steps()
    assert steps.index(VERSION_CHECK) < steps.index('Run the tests')


def test_the_version_is_checked_before_anything_is_tagged():
    steps = release_steps()
    assert steps.index(VERSION_CHECK) < steps.index('Tag it')
    assert steps.index(VERSION_CHECK) < steps.index('Publish the GitHub release')


def test_the_tests_run_before_anything_is_published():
    """A release that fails its own tests should not become an image anyone can
    pull."""
    steps = release_steps()
    assert steps.index('Run the tests') < steps.index('Tag it')
    assert steps.index('Run the tests') < steps.index('Publish the GitHub release')


def test_the_workflow_does_not_push_to_a_branch():
    """Pushing the bump would need a personal access token with write access to
    a public repo, because a ruleset bypass cannot name github-actions[bot].
    Only the tag is pushed, and tags are not covered by the branch ruleset."""
    body = (ROOT / '.github' / 'workflows' / 'release.yml').read_text()
    pushes = re.findall(r'git push origin (\S+)', body)
    assert pushes, 'the tag push disappeared'
    for target in pushes:
        assert 'HEAD:' not in target, f'{target} pushes to a branch'
    assert 'git commit' not in body


def test_the_image_build_is_called_not_left_to_the_release_trigger():
    """A release created with GITHUB_TOKEN does not start another workflow —
    GitHub blocks that to stop workflows triggering themselves. Relying on
    `release: published` here would mean the image was silently never built."""
    jobs = workflow('release.yml')['jobs']
    assert jobs['image']['uses'].endswith('docker-image.yml')
    assert jobs['image']['needs'] == 'release'


def test_the_publish_workflow_accepts_being_called():
    on = workflow('docker-image.yml')
    on = on[True] if True in on else on['on']
    assert 'workflow_call' in on
    assert 'ref' in on['workflow_call']['inputs']
    # and still works on its own, for a manual rebuild
    assert 'release' in on and 'workflow_dispatch' in on


def test_every_version_tag_falls_back_to_the_git_ref():
    """`inputs.ref` is only set when release.yml calls the publish workflow. On
    the `release` and `workflow_dispatch` triggers it is empty, and without the
    fallback a release published from the GitHub UI could build an image with no
    version tags at all — silently, since nothing fails."""
    on = workflow('docker-image.yml')['jobs']['publish']['steps']
    tags = next(s['with']['tags'] for s in on
                if str(s.get('uses', '')).startswith('docker/metadata-action'))
    semver = [line.strip() for line in tags.splitlines()
              if line.strip().startswith('type=semver')]
    assert semver, 'the version tags disappeared'
    for line in semver:
        assert 'inputs.ref || github.ref' in line, line


def test_the_release_job_may_write_to_the_repository():
    """It pushes a tag and creates a release; read-only would fail at the push."""
    assert workflow('release.yml')['permissions']['contents'] == 'write'


def test_the_check_finds_exactly_one_version_line():
    """The check refuses to guess when there is not exactly one, so a second
    __version__ line appearing would stop every release rather than silently
    verifying the wrong one."""
    source = (ROOT / 'app' / 'version.py').read_text()
    assert len(re.findall(r"__version__ = '([^']*)'", source)) == 1
