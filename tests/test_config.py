"""Database URL resolution.

Relative sqlite paths are resolved against the project root so the CLI and the
app open the same file wherever they are launched from — but that convenience
must not swallow SQLite's special in-memory forms, which are not filenames.
"""
import importlib
import os

import pytest

import config


def resolve(url, monkeypatch):
    if url is None:
        monkeypatch.delenv('DATABASE_URL', raising=False)
    else:
        monkeypatch.setenv('DATABASE_URL', url)
    importlib.reload(config)
    return config.Config.SQLALCHEMY_DATABASE_URI


@pytest.fixture(autouse=True)
def restore_config():
    yield
    importlib.reload(config)


def test_in_memory_is_left_alone(monkeypatch):
    """Resolving ':memory:' as a path creates a real file literally named
    ':memory:' in the project root."""
    assert resolve('sqlite:///:memory:', monkeypatch) == 'sqlite:///:memory:'


def test_the_empty_sqlite_form_is_left_alone(monkeypatch):
    """sqlite:// with no path is also in-memory."""
    assert resolve('sqlite://', monkeypatch) == 'sqlite://'


def test_a_relative_path_resolves_against_the_project_root(monkeypatch):
    resolved = resolve('sqlite:///data/app.db', monkeypatch)
    assert resolved == f'sqlite:///{os.path.join(config.BASE_DIR, "data/app.db")}'


def test_an_absolute_path_is_untouched(monkeypatch):
    assert resolve('sqlite:////tmp/somewhere.db', monkeypatch) == 'sqlite:////tmp/somewhere.db'


def test_the_default_is_the_instance_folder(monkeypatch):
    assert resolve(None, monkeypatch).endswith('instance/home_cmms.db')


def test_a_non_sqlite_url_passes_through(monkeypatch):
    url = 'postgresql://user:pw@localhost/db'
    assert resolve(url, monkeypatch) == url


def test_using_an_in_memory_database_creates_no_file(monkeypatch, tmp_path):
    """The regression this guards: a stray ':memory:' file in the repo."""
    from app import create_app
    from app.extensions import db

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///:memory:')
    importlib.reload(config)

    app = create_app(config.Config, {'TESTING': True, 'SCHEDULER_ENABLED': False,
                                     'UPLOAD_FOLDER': str(tmp_path / 'up')})
    with app.app_context():
        db.create_all()

    assert not (tmp_path / ':memory:').exists()
    assert not (config.BASE_DIR and os.path.exists(os.path.join(config.BASE_DIR, ':memory:')))
