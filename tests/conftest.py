import os
import tempfile

import pytest
from flask import g
from flask.testing import FlaskClient

os.environ['SCHEDULER_ENABLED'] = '0'

from app import create_app  # noqa: E402
from app.extensions import db as _db  # noqa: E402
from app.models.user import User  # noqa: E402

CSRF = 'test-csrf-token'


class IsolatedClient(FlaskClient):
    """A client whose requests do not inherit a cached logged-in user.

    The app fixture holds an app context open for the whole test so tests can
    touch the ORM directly. Flask normally pushes a fresh app context per
    request, which is what resets Flask-Login's `g._login_user` cache; with an
    outer context already active it does not, so one client signing in would
    leave every other client in the same test appearing authenticated. Dropping
    the cache before each request restores per-request loading, as in production.
    """

    def open(self, *args, **kwargs):
        g.pop('_login_user', None)
        return super().open(*args, **kwargs)


# ── prechecks ──────────────────────────────────────────────────────────────
#
# Some checks are structural and instant — does the version constant match the
# tag, does a workflow still refuse to publish a mismatch. They cost
# milliseconds and they are the ones most likely to be wrong when cutting a
# release, so waiting five minutes to be told is waste.
#
# Marking one `@pytest.mark.precheck` moves it to the front of the run *and*
# makes its failure end the session. That second half is the point: reordering
# alone would surface the problem in the first second but still grind through
# the other thousand tests, leaving you to notice and interrupt.

def pytest_collection_modifyitems(items):
    """Prechecks first. `sort` is stable, so everything else keeps its order."""
    items.sort(key=lambda item: 0 if item.get_closest_marker('precheck') else 1)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.failed and item.get_closest_marker('precheck'):
        item.session.shouldstop = (
            f'{item.name} is a precheck and it failed, so the rest of the suite '
            f'was not run. Fix this first, then run pytest again.'
        )
    return report


@pytest.fixture
def app():
    tmpdir = tempfile.TemporaryDirectory()
    db_path = os.path.join(tmpdir.name, 'test.db')

    application = create_app(config_overrides={
        'TESTING': True,
        'SECRET_KEY': 'test-secret',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{db_path}',
        'UPLOAD_FOLDER': os.path.join(tmpdir.name, 'uploads'),
        'SCHEDULER_ENABLED': False,
        'WTF_CSRF_ENABLED': False,
    })
    os.makedirs(application.config['UPLOAD_FOLDER'], exist_ok=True)

    application.test_client_class = IsolatedClient

    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
    tmpdir.cleanup()


@pytest.fixture
def db(app):
    return _db


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(username='tester', role='user', password='password123', active=True):
    user = User(username=username, email=f'{username}@example.com', role=role, is_active=active)
    user.set_password(password)
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture
def admin(app):
    return make_user('admin', role='admin')


@pytest.fixture
def user(app):
    return make_user('tester')


def prime_csrf(client):
    """Seed the session CSRF token so tests can post without scraping a form."""
    with client.session_transaction() as sess:
        sess['csrf_token'] = CSRF
    return CSRF


@pytest.fixture
def login(client):
    def _login(username='tester', password='password123'):
        prime_csrf(client)
        response = client.post('/auth/login', data={
            'username': username, 'password': password, 'csrf_token': CSRF,
        })
        prime_csrf(client)
        return response
    return _login
