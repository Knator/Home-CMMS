"""Things a container deployment depends on."""
import os
import pathlib
import re
import subprocess
import sys

import pytest

from app.models.auth_attempt import AuthAttempt
from app.models.user import User


# ── unattended admin creation ──────────────────────────────────────────────

def run_create_admin(app, *args, env=None):
    """Run the script against this test's database, as the entrypoint would."""
    environment = {
        **os.environ,
        'SCHEDULER_ENABLED': '0',
        'SECRET_KEY': 'test-secret',
        'DATABASE_URL': app.config['SQLALCHEMY_DATABASE_URI'],
        'UPLOAD_FOLDER': app.config['UPLOAD_FOLDER'],
        **(env or {}),
    }
    return subprocess.run([sys.executable, 'create_admin.py', *args],
                          capture_output=True, text=True, env=environment, timeout=60)


def test_an_admin_can_be_created_without_a_terminal(app, db):
    result = run_create_admin(app, '--username', 'boss', '--email', 'boss@example.com',
                              '--password', 'A-long-enough-password1')
    assert result.returncode == 0, result.stderr
    assert User.query.filter_by(username='boss').one().role == 'admin'


def test_environment_variables_work_too(app, db):
    result = run_create_admin(app, env={'ADMIN_USERNAME': 'boss',
                                        'ADMIN_EMAIL': 'boss@example.com',
                                        'ADMIN_PASSWORD': 'A-long-enough-password1'})
    assert result.returncode == 0, result.stderr
    assert User.query.filter_by(username='boss').first() is not None


def test_if_missing_is_safe_to_repeat(app, db):
    """The entrypoint runs it on every start."""
    args = ('--if-missing', '--username', 'boss', '--email', 'boss@example.com',
            '--password', 'A-long-enough-password1')
    first = run_create_admin(app, *args)
    second = run_create_admin(app, *args)

    assert first.returncode == 0 and second.returncode == 0
    assert 'already exists' in second.stdout
    assert User.query.filter_by(username='boss').count() == 1


def test_a_weak_password_is_refused(app, db):
    result = run_create_admin(app, '--username', 'boss', '--email', 'boss@example.com',
                              '--password', 'short')
    assert result.returncode != 0
    assert User.query.filter_by(username='boss').first() is None


def test_it_fails_clearly_with_no_terminal_and_no_details(app, db):
    result = subprocess.run(
        [sys.executable, 'create_admin.py'],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
        env={**os.environ, 'SCHEDULER_ENABLED': '0', 'SECRET_KEY': 'test-secret',
             'DATABASE_URL': app.config['SQLALCHEMY_DATABASE_URI'],
             'UPLOAD_FOLDER': app.config['UPLOAD_FOLDER']},
    )
    assert result.returncode != 0
    assert 'ADMIN_' in result.stderr


# ── proxy headers ──────────────────────────────────────────────────────────

def sign_in_badly(client, forwarded='203.0.113.42', direct='172.18.0.1'):
    from tests.conftest import CSRF, prime_csrf
    prime_csrf(client)
    return client.post('/auth/login',
                       data={'username': 'tester', 'password': 'wrong', 'csrf_token': CSRF},
                       headers={'X-Forwarded-For': forwarded},
                       environ_base={'REMOTE_ADDR': direct})


def test_forwarded_addresses_are_ignored_by_default(client, db, user):
    """Trusting the header without a proxy would let any client forge its
    address and sidestep the rate limit."""
    sign_in_badly(client)
    assert AuthAttempt.query.one().ip_address == '172.18.0.1'


def test_forwarded_addresses_are_used_when_trusted(app, db, user):
    app.config['TRUST_PROXY_HEADERS'] = True
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    sign_in_badly(app.test_client())
    assert AuthAttempt.query.one().ip_address == '203.0.113.42'


def test_the_setting_defaults_to_off(app):
    assert app.config.get('TRUST_PROXY_HEADERS') is not True


# ── the deployment files ───────────────────────────────────────────────────

def read(path):
    with open(path) as handle:
        return handle.read()


def test_compose_file_is_valid():
    yaml = pytest.importorskip('yaml')
    compose = yaml.safe_load(read('docker-compose.yml'))
    service = compose['services']['cmms']
    assert service['restart'] == 'unless-stopped'
    # Both data directories must be volumes or an update destroys everything.
    mounted = ' '.join(service['volumes'])
    assert '/app/instance' in mounted and '/app/uploads' in mounted


def test_the_image_does_not_run_as_root():
    assert 'USER cmms' in read('Dockerfile')


def test_only_one_worker_is_started():
    """SQLite takes one writer, and a second scheduler would duplicate PMs."""
    assert '--workers 1' in read('docker/entrypoint.sh')


def test_migrations_run_before_serving():
    entrypoint = read('docker/entrypoint.sh')
    assert entrypoint.index('flask db upgrade') < entrypoint.index('exec gunicorn')


def test_secrets_and_data_are_never_baked_into_the_image():
    ignored = read('.dockerignore')
    for path in ('.env', 'instance/', '.venv/', '.git/'):
        assert path in ignored, path


def test_the_env_example_documents_every_variable_compose_reads():
    yaml = pytest.importorskip('yaml')
    compose = yaml.safe_load(read('docker-compose.yml'))
    example = read('.env.docker.example')

    import re
    referenced = set(re.findall(r'\$\{([A-Z_]+)', yaml.dump(compose)))
    undocumented = [name for name in referenced if name not in example]
    assert not undocumented, f'not explained in .env.docker.example: {undocumented}'


def test_the_documentation_covers_what_it_must():
    doc = read('DOCKER.md')
    for topic in ('Environment variables', 'What lives where', 'Backups', 'Restoring',
                  'docker compose', 'TZ', 'FLASK_ENV', 'TRUST_PROXY_HEADERS'):
        assert topic in doc, topic


def test_the_backup_warning_is_present():
    """A plain copy of a WAL-mode database silently loses recent writes."""
    doc = read('DOCKER.md')
    assert 'WAL' in doc
    assert 'VACUUM INTO' in doc


# ── test tooling must not ship to production ───────────────────────────────
#
# requirements.txt is what the Docker image installs, so anything added there
# reaches every deployment. A JavaScript engine and a test runner in a runtime
# container are code that can never run correctly but can still carry a
# vulnerability.

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEV_ONLY = ('pytest', 'dukpy', 'PyYAML')


def requirement_names(filename):
    """Package names from a requirements file, ignoring comments and -r lines."""
    text = (ROOT / filename).read_text()
    names = []
    for line in text.split('\n'):
        line = line.split('#')[0].strip()
        if not line or line.startswith('-'):
            continue
        names.append(re.split(r'[<>=!~\[]', line)[0].strip())
    return names


@pytest.mark.parametrize('package', DEV_ONLY)
def test_test_tooling_is_not_a_runtime_dependency(package):
    assert package not in requirement_names('requirements.txt'), (
        f'{package} would be installed into the production image'
    )


@pytest.mark.parametrize('package', DEV_ONLY)
def test_test_tooling_is_still_available_to_developers(package):
    assert package in requirement_names('requirements-dev.txt')


def test_the_dev_file_includes_the_runtime_one():
    """So `pip install -r requirements-dev.txt` is the only command a
    contributor needs."""
    assert '-r requirements.txt' in (ROOT / 'requirements-dev.txt').read_text()


def test_the_image_installs_only_the_runtime_file():
    dockerfile = (ROOT / 'Dockerfile').read_text()
    assert 'requirements.txt' in dockerfile
    assert 'requirements-dev.txt' not in dockerfile


@pytest.mark.parametrize('package', ['Flask', 'Werkzeug', 'Flask-SQLAlchemy',
                                     'Flask-Login', 'Flask-Migrate',
                                     'APScheduler', 'python-dotenv', 'Pillow'])
def test_what_the_app_actually_needs_is_still_there(package):
    """The split must not have dropped a runtime dependency: the app imports
    every one of these, and the container installs nothing else."""
    assert package in requirement_names('requirements.txt')
