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

    # Only enable behind a reverse proxy you control. If nothing strips
    # X-Forwarded-For, a client can set it freely and both spoof its address in
    # the audit log and dodge the per-address rate limit.
    # Optionally bound the first-run setup window, the way Portainer does. 0
    # leaves it open until an account is created, which is what most self-hosted
    # projects do.
    SETUP_WINDOW_MINUTES = int(os.environ.get('SETUP_WINDOW_MINUTES', '0') or 0)

    TRUST_PROXY_HEADERS = os.environ.get('TRUST_PROXY_HEADERS', '') in ('1', 'true', 'True', 'yes')

    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or os.path.join(BASE_DIR, 'uploads')
    # Applies to any single request, so it caps both an attachment and an
    # uploaded backup archive. Raise it if you restore by upload and your
    # archive is larger; restoring from instance/backups has no such limit.
    # 100 MB by default: a phone clip of a fault is easily tens of megabytes,
    # and a video of the noise a pump is making is often the whole point of the
    # attachment.
    MAX_UPLOAD_MB = int(os.environ.get('MAX_UPLOAD_MB', '100') or 100)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

    # Everything here is only ever handed back by the download route, which
    # always sends as_attachment=True — the browser saves it rather than
    # rendering it. The inline route serves IMAGE_EXTENSIONS and nothing else.
    #
    # Still an allowlist rather than a denylist, and it deliberately holds no
    # executable or script format (exe, msi, bat, cmd, ps1, sh, jar, js, vbs)
    # and no markup that a browser would run if it ever were rendered (html,
    # htm, xhtml, svg). Nothing in the app executes an upload, so this is depth
    # rather than the only defence: it stops the instance being used as a
    # convenient place to host someone else's malware.
    DOCUMENT_EXTENSIONS = {
        'pdf', 'doc', 'docx', 'odt', 'rtf', 'txt', 'md',
        'xls', 'xlsx', 'ods', 'csv', 'tsv',
        'ppt', 'pptx', 'odp',
    }
    IMAGE_FILE_EXTENSIONS = {
        'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif',
        # Raw and phone formats: kept downloadable even though the browser
        # cannot display them and Pillow cannot thumbnail them unaided.
        'heic', 'heif', 'tif', 'tiff', 'dng', 'raw', 'cr2', 'nef', 'arw',
    }
    VIDEO_EXTENSIONS = {
        'mp4', 'm4v', 'mov', 'webm', 'mkv', 'avi', 'wmv', 'mpg', 'mpeg', '3gp',
    }
    AUDIO_EXTENSIONS = {
        'mp3', 'm4a', 'wav', 'aac', 'ogg', 'oga', 'opus', 'flac', 'wma',
    }
    # 2D drawings, 3D models and the native formats of the common packages.
    CAD_EXTENSIONS = {
        'dwg', 'dxf', 'dwf', 'dgn',
        'step', 'stp', 'iges', 'igs', 'stl', '3mf', 'obj', 'ply', '3ds',
        'skp', 'f3d', 'f3z', 'sldprt', 'sldasm', 'slddrw',
        'ipt', 'iam', 'idw', 'catpart', 'catproduct', 'prt', 'asm',
        'scad', 'gcode', 'x_t', 'x_b', 'sat',
    }
    ARCHIVE_EXTENSIONS = {'zip', '7z', 'tar', 'gz', 'tgz', 'bz2', 'xz', 'rar'}
    DATA_EXTENSIONS = {'json', 'xml', 'yaml', 'yml', 'log', 'ics', 'eml', 'vcf'}

    ALLOWED_EXTENSIONS = (
        DOCUMENT_EXTENSIONS | IMAGE_FILE_EXTENSIONS | VIDEO_EXTENSIONS
        | AUDIO_EXTENSIONS | CAD_EXTENSIONS | ARCHIVE_EXTENSIONS
        | DATA_EXTENSIONS
    )

    # What the inline route will serve and what thumbnails are attempted for:
    # raster formats a browser can actually render. SVG is excluded because it
    # can carry script, and HEIC/TIFF/raw because the browser cannot show them —
    # they are downloadable, just not previewable.
    IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif'}

    # Served inline, so the browser renders them instead of saving them. Wider
    # than IMAGE_EXTENSIONS because a PDF manual or a clip of a fault is worth
    # looking at without downloading first — but still a curated list, because
    # inline is the disposition where content type matters.
    #
    # Everything here is either inert to a browser (raster images, video, audio)
    # or forced to text/plain below. Nothing that a browser would parse as
    # markup is in it, and `html`, `htm`, `xhtml` and `svg` cannot be uploaded
    # in the first place. Responses carry nosniff, so the declared type is the
    # one the browser uses.
    TEXT_VIEW_EXTENSIONS = {'txt', 'md', 'log', 'csv', 'tsv', 'json', 'xml',
                            'yaml', 'yml'}
    VIEWABLE_EXTENSIONS = (
        IMAGE_EXTENSIONS
        | {'pdf'}
        | {'mp4', 'm4v', 'mov', 'webm', 'ogg'}          # what browsers play
        | {'mp3', 'm4a', 'wav', 'aac', 'oga', 'opus', 'flac'}
        | TEXT_VIEW_EXTENSIONS
    )
