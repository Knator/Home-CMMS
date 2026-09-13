"""Telling an administrator whether a newer release exists.

This is the **only** outbound network call in the application. Everything else
works with no internet at all, and that is worth preserving, so:

  * it is a setting, and can be switched off;
  * the result is cached, so the page does not call out on every load;
  * the request has a short timeout and every failure is swallowed — an
    instance with no route to the internet shows "could not check" and carries
    on, rather than hanging the maintenance page for whoever opened it;
  * the URL is a constant. Nothing user-supplied reaches it, so this adds no
    SSRF surface.

Nothing about the instance is sent. It is an unauthenticated GET of a public
release listing; GitHub sees an IP address and nothing else.
"""
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from app.extensions import db
from app.models.setting import Setting
from app.utils import utcnow
from app.version import __version__

log = logging.getLogger(__name__)

RELEASES_URL = 'https://api.github.com/repos/Knator/Home-CMMS/releases/latest'
RELEASES_PAGE = 'https://github.com/Knator/Home-CMMS/releases/latest'

# How long a result stands before asking again. GitHub allows 60 unauthenticated
# requests an hour per address; this keeps a busy admin nowhere near it.
CACHE_FOR = timedelta(hours=6)

# Short: this runs while somebody is waiting for a page to render.
TIMEOUT_SECONDS = 3

# Cache rows, kept in the settings table but not in DEFAULTS — they are not
# preferences and have no place on the settings page.
_LATEST = '_update_latest_version'
_CHECKED = '_update_checked_at'
_ERROR = '_update_error'


def current_version():
    return __version__


def version_parts(value):
    """A comparable tuple from a version string, or () if it is not one.

    `v1.2.3` and `1.2.3` compare the same. A pre-release suffix is dropped for
    the numeric comparison and handled separately by `compare()`, so 1.0.1-dev
    sorts below 1.0.1 rather than equal to it.
    """
    if not value:
        return ()
    core = str(value).strip().lstrip('vV').split('-')[0].split('+')[0]
    parts = []
    for piece in core.split('.'):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts)


def _is_prerelease(value):
    return '-' in str(value or '').strip().lstrip('vV')


def compare(running, latest):
    """'behind', 'current', or 'ahead'. Unknown input reads as 'current'."""
    a, b = version_parts(running), version_parts(latest)
    if not a or not b:
        return 'current'
    if a != b:
        return 'behind' if a < b else 'ahead'
    # Same numbers: a pre-release of that version is still behind the release.
    if _is_prerelease(running) and not _is_prerelease(latest):
        return 'behind'
    return 'current'


def _get(key):
    row = db.session.get(Setting, key)
    return row.value if row else None


def _put(key, value):
    row = db.session.get(Setting, key)
    if row is None:
        row = Setting(key=key)
        db.session.add(row)
    row.value = value if value is None else str(value)


def _fetch_latest():
    """The newest published release tag, or None. Never raises."""
    request = urllib.request.Request(
        RELEASES_URL,
        headers={'Accept': 'application/vnd.github+json',
                 'User-Agent': f'Home-CMMS/{__version__}'},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    return payload.get('tag_name')


def check(force=False):
    """Look up the latest release, honouring the cache. Never raises.

    Returns the status dict `status()` reports, so a caller that wants a fresh
    answer can ask for one.
    """
    from app import settings as app_settings

    if not app_settings.get('update_check_enabled'):
        return status(enabled=False)

    checked_at = _get(_CHECKED)
    if not force and checked_at:
        try:
            last = datetime.fromisoformat(checked_at)
            if utcnow() - last < CACHE_FOR:
                return status()
        except ValueError:
            pass                                 # unparseable: check again

    try:
        latest = _fetch_latest()
        _put(_LATEST, latest)
        _put(_ERROR, None)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        # No route, no DNS, GitHub down, rate limited, or a body that is not
        # JSON. None of these is worth an error page.
        log.info('Update check failed: %s', error)
        _put(_ERROR, str(error)[:200])
    _put(_CHECKED, utcnow().isoformat())
    db.session.commit()
    return status()


def status(enabled=None):
    """What the maintenance page shows. Reads the cache; makes no request."""
    from app import settings as app_settings

    if enabled is None:
        try:
            enabled = app_settings.get('update_check_enabled')
        except Exception:
            enabled = False

    latest = _get(_LATEST)
    return {
        'enabled': bool(enabled),
        'current': current_version(),
        'latest': latest,
        'state': compare(current_version(), latest) if latest else 'unknown',
        'checked_at': _get(_CHECKED),
        'error': _get(_ERROR),
        'releases_url': RELEASES_PAGE,
    }
