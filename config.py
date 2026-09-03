import os
import secrets
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
        # ":memory:" is SQLite's in-memory database, not a filename. Resolving
        # it against the project root turns it into a real file called
        # ":memory:" — which is how one ended up committed-adjacent in the repo.
        if path in ('', ':memory:'):
            return url
        if not os.path.isabs(path):
            return f"sqlite:///{os.path.join(BASE_DIR, path)}"
        return url
    if '://' not in url:
        return f"sqlite:///{os.path.abspath(os.path.join(BASE_DIR, url))}"
    return url


SECRET_KEY_FILE = os.path.join(INSTANCE_DIR, 'secret_key')


def _secret_key():
    """The signing key, in order of preference: environment, file, generated.

    There is deliberately no hardcoded fallback. A constant compiled into the
    source would be identical for every self-hosted install, and anyone holding
    it can forge a session cookie for any account — so a shipped default is the
    same as no authentication at all.

    When nothing is configured we generate one and persist it, so sessions
    survive a restart. Keep `instance/` on a volume in a container, or sessions
    reset whenever the container is replaced.
    """
    from_env = os.environ.get('SECRET_KEY', '').strip()
    if from_env:
        return from_env

    try:
        with open(SECRET_KEY_FILE, 'r', encoding='utf-8') as handle:
            stored = handle.read().strip()
        if stored:
            return stored
    except FileNotFoundError:
        pass
    except OSError as error:
        raise RuntimeError(
            f'Could not read {SECRET_KEY_FILE} ({error}). Set SECRET_KEY in the '
            'environment instead.'
        ) from error

    generated = secrets.token_hex(32)
    try:
        os.makedirs(INSTANCE_DIR, exist_ok=True)
        # Written 0600 before anything is in it, so the key is never briefly
        # world-readable.
        handle = os.open(SECRET_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, 'w', encoding='utf-8') as fh:
            fh.write(generated)
    except FileExistsError:
        # Another worker won the race; use whatever it wrote.
        with open(SECRET_KEY_FILE, 'r', encoding='utf-8') as fh:
            return fh.read().strip()
    except OSError as error:
        raise RuntimeError(
            f'No SECRET_KEY is set and {SECRET_KEY_FILE} could not be created '
            f'({error}). Set SECRET_KEY in the environment.'
        ) from error
    return generated


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
    # Applies because the login route marks the session permanent. Flask
    # refreshes it on each request, so this is 8 hours of inactivity, not 8
    # hours from sign-in.
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # "Remember me" is a separate, longer-lived cookie that Flask-Login manages,
    # and it does NOT inherit the SESSION_COOKIE_* settings above. Left alone it
    # is a year-long login token with no Secure and no SameSite — the more
    # valuable of the two cookies, protected less well than the session.
    #
    # Secure is conditional for the same reason the session's is: a Secure
    # cookie is never sent over plain http, so forcing it on would silently
    # break "remember me" for a LAN install that has no TLS.
    REMEMBER_COOKIE_SECURE = os.environ.get('FLASK_ENV') == 'production'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_DURATION = timedelta(days=30)

    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB
    ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp',
                          'doc', 'docx', 'txt', 'xlsx', 'csv', 'zip'}
    # Raster formats only. SVG is deliberately excluded: it can carry script and
    # these are served inline rather than as a download.
    IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
