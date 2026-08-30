import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
DEFAULT_DB_PATH = os.path.join(INSTANCE_DIR, 'home_cmms.db')


def _database_uri():
    """SQLite by default; DATABASE_URL overrides it.

    A bare filesystem path (or a relative sqlite:/// URL) is resolved against the
    project root so the CLI and the app always open the same file regardless of
    the directory they were launched from.
    """
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        return f"sqlite:///{DEFAULT_DB_PATH}"
    if url.startswith('sqlite:///'):
        path = url[len('sqlite:///'):]
        if path and not os.path.isabs(path):
            return f"sqlite:///{os.path.join(BASE_DIR, path)}"
        return url
    if '://' not in url:
        return f"sqlite:///{os.path.abspath(os.path.join(BASE_DIR, url))}"
    return url


def _secret_key():
    key = os.environ.get('SECRET_KEY', '').strip()
    if key:
        return key
    if os.environ.get('FLASK_ENV') == 'production':
        raise RuntimeError(
            "SECRET_KEY must be set when FLASK_ENV=production. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    return 'dev-secret-change-in-production'


class Config:
    SECRET_KEY = _secret_key()
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Wait rather than fail immediately when the hourly PM scheduler and a web
    # request try to write at the same time; SQLite allows only one writer.
    SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 30}}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('FLASK_ENV') == 'production'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB
    ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'txt', 'xlsx', 'csv', 'zip'}
