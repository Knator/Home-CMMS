"""Typed access to the in-app preferences.

Adding a preference means adding an entry to DEFAULTS and nothing else — no
migration, because the store is key/value.

Reads are cached per request. The alternative is a query every time anything
asks whether archived work orders may be deleted, which is on the path of every
work order page.
"""
from flask import current_app, g

from app.extensions import db
from app.models.setting import Setting

# key -> (kind, default, env var that overrides it, human label)
DEFAULTS = {
    'allow_archived_deletion': ('bool', True, None,
                                'Allow archived work orders to be deleted'),
    'upload_limit_enabled': ('bool', True, None,
                             'Enforce a maximum attachment size'),
    'max_upload_mb': ('int', 100, 'MAX_UPLOAD_MB',
                      'Maximum attachment size, in MB'),
    # Off by default: auto-archiving changes records without anyone asking, and
    # archiving cannot be undone. Opting in should be a decision.
    'auto_archive_enabled': ('bool', False, None,
                             'Automatically archive closed work orders'),
    'auto_archive_days': ('int', 90, None,
                          'Days a closed work order waits before archiving'),
}

# What "no limit" means when the limit is switched off. Werkzeug has no
# unlimited sentinel, so this is a ceiling far above any real attachment.
NO_UPLOAD_LIMIT = 64 * 1024 ** 3


def _cache():
    if not hasattr(g, '_settings_cache'):
        g._settings_cache = {}
    return g._settings_cache


def begin_request():
    """Drop the cache at the start of each request.

    The cache lives on `g`, which is per *app context*. Flask normally pushes
    one per request, so that is the same thing — but not when an outer app
    context is already active, which is how the test suite runs and how any
    CLI-style caller behaves. Clearing it explicitly makes a stale value
    impossible rather than merely unlikely.
    """
    g._settings_cache = {}


def _default_for(key):
    """The value to use when nothing is stored and no variable is set.

    For the upload limit that is MAX_CONTENT_LENGTH, so the configured cap stays
    the default rather than being quietly ignored: config sets the starting
    point, the Settings page overrides it, an environment variable locks it.
    """
    default = DEFAULTS[key][1]
    if key == 'max_upload_mb':
        configured = current_app.config.get('MAX_CONTENT_LENGTH')
        if configured:
            return max(1, int(configured) // (1024 * 1024))
    return default


def _coerce(kind, raw, default):
    if raw is None:
        return default
    if kind == 'bool':
        return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')
    if kind == 'int':
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return default
    return raw


def env_override(key):
    """The environment's value for this preference, or None.

    An operator who sets a variable meant it; the form shows the field as locked
    rather than pretending a change will stick.
    """
    kind, _default, var, _label = DEFAULTS[key]
    default = _default_for(key)
    if not var:
        return None
    raw = current_app.config.get('ENV_SETTING_OVERRIDES', {}).get(var)
    if raw is None or str(raw).strip() == '':
        return None
    return _coerce(kind, raw, default)


def get(key):
    """The effective value: environment if set, else stored, else the default."""
    kind, _default, _var, _label = DEFAULTS[key]
    default = _default_for(key)
    cache = _cache()
    if key in cache:
        return cache[key]

    value = env_override(key)
    if value is None:
        try:
            row = db.session.get(Setting, key)
        except Exception:
            # Before the table exists — during first-run setup or a migration —
            # the defaults are the right answer rather than a crash.
            row = None
        value = _coerce(kind, row.value if row else None, default)

    cache[key] = value
    return value


def set_value(key, value, user_id=None):
    """Store a preference. Ignored for keys the environment has taken over."""
    if key not in DEFAULTS:
        raise KeyError(key)
    if env_override(key) is not None:
        return False

    row = db.session.get(Setting, key)
    if row is None:
        row = Setting(key=key)
        db.session.add(row)
    row.value = str(value)
    row.updated_by = user_id
    _cache().pop(key, None)
    return True


def all_settings():
    """Every preference with its value and where that value came from."""
    out = {}
    for key, (kind, _default, var, label) in DEFAULTS.items():
        default = _default_for(key)
        override = env_override(key)
        out[key] = {
            'key': key, 'kind': kind, 'label': label, 'default': default,
            'value': get(key),
            'env_var': var,
            'locked': override is not None,
        }
    return out


# ── what the preferences actually mean ─────────────────────────────────────

def explicit_mb():
    """The limit someone actually chose — environment, then stored. None if
    neither, meaning nothing has overridden the configured default."""
    value = env_override('max_upload_mb')
    if value is not None:
        return value
    try:
        row = db.session.get(Setting, 'max_upload_mb')
    except Exception:
        return None
    if row is None or row.value is None:
        return None
    return _coerce('int', row.value, None)


def upload_limit_bytes():
    """The cap to apply to an ordinary upload.

    A chosen value is in whole MB. Nothing chosen falls back to
    MAX_CONTENT_LENGTH **in bytes**, not converted to MB and back: the setting's
    granularity should not coarsen a limit that was configured precisely.
    """
    if not get('upload_limit_enabled'):
        return NO_UPLOAD_LIMIT

    chosen = explicit_mb()
    if chosen is not None:
        return max(1, chosen) * 1024 * 1024

    configured = current_app.config.get('MAX_CONTENT_LENGTH')
    if configured:
        return int(configured)
    return DEFAULTS['max_upload_mb'][1] * 1024 * 1024


def archived_deletion_allowed():
    return get('allow_archived_deletion')


def auto_archive_after_days():
    """How many days a closed work order waits, or None when switched off."""
    if not get('auto_archive_enabled'):
        return None
    return max(0, get('auto_archive_days'))
