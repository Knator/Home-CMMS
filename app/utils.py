import os
import secrets
import shutil
import uuid
from datetime import date, datetime, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import session, request, abort, redirect, url_for, flash, current_app
from flask_login import current_user
from werkzeug.utils import secure_filename


def utcnow():
    """Naive UTC timestamp.

    datetime.utcnow() is deprecated from Python 3.12 on; this keeps the same
    stored value (naive, UTC) without the warning.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(value):
    """Interpret a stored timestamp in the host's timezone.

    Timestamps are stored as UTC and converted only for display. Storing local
    time instead would be simpler but lossy: during a daylight-saving fallback
    the same wall-clock hour occurs twice, so 01:30 is ambiguous and ordering
    breaks; during the spring jump some times never happen at all. Worse, the
    stored values silently change meaning if the machine's timezone ever
    changes.

    astimezone() with no argument uses the operating system's timezone, so this
    needs no timezone database of its own and no network — set TZ in a container
    and it follows.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone()


def format_datetime(value, fmt='%Y-%m-%d %H:%M', empty='—'):
    """A stored timestamp rendered in host-local time."""
    local = to_local(value)
    return local.strftime(fmt) if local else empty


def local_timezone_name():
    """What the host calls its timezone, e.g. 'EDT'. Shown so the displayed
    times can be sanity-checked."""
    now = datetime.now().astimezone()
    offset = now.strftime('%z')
    return f"{now.tzname()} (UTC{offset[:3]}:{offset[3:]})" if offset else (now.tzname() or 'unknown')


def parse_date(value):
    """ISO date string -> date, or None if absent/malformed."""
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def parse_int(value, minimum=None):
    """Int from untrusted input, or None. Never raises on junk query strings."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    return parsed


def choice(value, allowed, default):
    """Keep a posted value inside a known vocabulary.

    A forged <select> otherwise stores a status that badge styling, filters and
    business rules know nothing about.
    """
    return value if value in allowed else default


def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def validate_csrf():
    token = request.form.get('csrf_token')
    expected = session.get('csrf_token')
    if not token or not expected or not secrets.compare_digest(token, expected):
        abort(403)


def is_safe_redirect_url(target):
    """True only for same-origin relative paths, so ?next= cannot send a user off-site."""
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc and target.startswith('/') and not target.startswith('//')


def safe_redirect(target, fallback_endpoint='main.dashboard'):
    if is_safe_redirect_url(target):
        return redirect(target)
    return redirect(url_for(fallback_endpoint))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Administrator access required.', 'error')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated


def allowed_file(filename):
    return (
        '.' in filename and
        filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']
    )


def is_image_file(filename):
    return (
        '.' in filename and
        filename.rsplit('.', 1)[1].lower() in current_app.config['IMAGE_EXTENSIONS']
    )


def entity_upload_dir(entity_type, entity_id):
    return os.path.join(current_app.config['UPLOAD_FOLDER'], entity_type, str(entity_id))


def save_attachment(file, entity_type, entity_id):
    original_filename = secure_filename(file.filename) or 'upload'
    stored_filename = f"{uuid.uuid4().hex}_{original_filename}"
    upload_dir = entity_upload_dir(entity_type, entity_id)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, stored_filename)
    file.save(file_path)
    return stored_filename, original_filename, os.path.getsize(file_path), file.content_type


MAX_UPLOAD_ROWS = 10


def named_uploads(files, display_name=None):
    """Pair uploaded files with a friendly name.

    A name only makes sense for a single file — applying one label to a whole
    selection would be misleading — so it is dropped when several were chosen
    and those keep their filenames.
    """
    real = [f for f in files if f and f.filename]
    if not real:
        return []
    if display_name and len(real) == 1:
        return [(real[0], display_name)]
    return [(f, None) for f in real]


def upload_rows_from_form(prefix='attachment', max_rows=MAX_UPLOAD_ROWS):
    """Read repeatable [files][optional name] rows off a submitted form.

    Each row's input accepts several files at once, so one row can carry a whole
    selection. The row count comes from a hidden field the browser maintains, so
    it is untrusted: parsed defensively and capped.
    """
    count = parse_int(request.form.get(f'{prefix}_count'), minimum=0) or 0
    rows = []
    for i in range(min(count, max_rows)):
        files = request.files.getlist(f'{prefix}_{i}_file')
        rows.extend(named_uploads(files, request.form.get(f'{prefix}_{i}_name', '').strip() or None))
    return rows


def store_uploads(entity_type, entity_id, rows, uploaded_by):
    """Validate and persist a batch of uploads. Returns (attachments, errors).

    The created rows come back so a caller can reference one (the asset photo
    does). Rejected files are reported rather than aborting the batch — one bad
    extension should not discard the other files or the form submission that
    carried them.
    """
    from app.extensions import db
    from app.models.attachment import Attachment

    saved, errors = [], []
    for file, display_name in rows:
        if not allowed_file(file.filename):
            errors.append(f"'{file.filename}' was not saved — that file type is not allowed.")
            continue
        stored, original, size, mime = save_attachment(file, entity_type, entity_id)
        attachment = Attachment(
            entity_type=entity_type, entity_id=entity_id,
            stored_filename=stored, original_filename=original,
            display_name=display_name, file_size=size, mime_type=mime,
            uploaded_by=uploaded_by,
        )
        db.session.add(attachment)
        saved.append(attachment)
    return saved, errors


# Longest edge of a generated thumbnail. Rendered around 96px, so this keeps a
# high-DPI screen sharp without shipping the original.
THUMBNAIL_MAX_PX = 320

# When a thumbnail cannot be produced we may serve the original instead, but
# only if it is small. Sending a 13 MB photo to fill a 48px box makes scrolling
# stall for seconds — far worse than showing no preview at all.
THUMBNAIL_FALLBACK_MAX_BYTES = 1024 * 1024

# Pillow refuses images above ~179 MP as suspected decompression bombs. Phone
# panorama and super-resolution modes legitimately exceed that (a 16320x12240
# shot is 200 MP), and these are files an authenticated user uploaded to their
# own instance. Raised, but still bounded — and draft() below means such a file
# is never decoded at full size anyway.
THUMBNAIL_MAX_PIXELS = 600_000_000


def thumbnails_available():
    """Whether Pillow is importable, so previews can actually be generated."""
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def thumbnail_dir():
    return os.path.join(current_app.config['UPLOAD_FOLDER'], '.thumbnails')


def thumbnail_path(attachment_id):
    return os.path.join(thumbnail_dir(), f'{attachment_id}.jpg')


def build_thumbnail(source_path, attachment_id):
    """Generate and cache a thumbnail, returning its path (None if it can't be made).

    Cached on disk because resizing a phone photo on every page view is wasteful
    and the source never changes — a new upload is a new attachment id.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:          # Pillow is optional; callers fall back
        return None

    target = thumbnail_path(attachment_id)
    if os.path.exists(target):
        return target

    if Image.MAX_IMAGE_PIXELS is not None and Image.MAX_IMAGE_PIXELS < THUMBNAIL_MAX_PIXELS:
        Image.MAX_IMAGE_PIXELS = THUMBNAIL_MAX_PIXELS

    try:
        os.makedirs(thumbnail_dir(), exist_ok=True)
        with Image.open(source_path) as img:
            # Ask the JPEG decoder for a reduced-scale read before anything
            # touches the pixels. A 200 MP photo would otherwise need ~600 MB of
            # RAM to decode; drafting brings that down by up to 64x.
            img.draft('RGB', (THUMBNAIL_MAX_PX, THUMBNAIL_MAX_PX))
            # Phone photos carry their rotation in EXIF; without this they come
            # out sideways.
            img = ImageOps.exif_transpose(img)
            if img.mode not in ('RGB', 'L'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background
            img.thumbnail((THUMBNAIL_MAX_PX, THUMBNAIL_MAX_PX), Image.LANCZOS)
            img.save(target, 'JPEG', quality=82, optimize=True)
        return target
    except Exception:
        current_app.logger.exception('Could not thumbnail attachment %s', attachment_id)
        return None


def discard_thumbnail(attachment_id):
    """Drop a cached thumbnail. Safe to call for non-images."""
    try:
        path = thumbnail_path(attachment_id)
    except RuntimeError:         # outside an app context
        return
    if os.path.exists(path):
        os.remove(path)


def purge_entity_attachments(entity_type, entity_id):
    """Delete an entity's attachment rows and its upload directory.

    Attachments are polymorphic (entity_type + entity_id, no foreign key), so
    nothing in the database cleans them up when the parent row goes away. Every
    entity delete route must call this or the rows and files are orphaned.
    """
    from app.extensions import db
    from app.models.attachment import Attachment

    doomed = Attachment.query.filter_by(entity_type=entity_type, entity_id=entity_id).all()
    for attachment in doomed:
        discard_thumbnail(attachment.id)

    Attachment.query.filter_by(entity_type=entity_type, entity_id=entity_id).delete(
        synchronize_session=False
    )
    shutil.rmtree(entity_upload_dir(entity_type, entity_id), ignore_errors=True)


def format_duration(minutes):
    """Minutes as a short human duration: 45m, 1h 30m, 2h."""
    if not minutes:
        return '—'
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f'{hours}h {mins}m'
    if hours:
        return f'{hours}h'
    return f'{mins}m'


def format_file_size(size_bytes):
    """Bytes as a short human size.

    Goes up to TB because the maintenance page reports disk capacity with
    this, and a 500 GB volume rendered as "512000.0 MB" is unreadable.
    """
    if size_bytes is None:
        return '—'
    if size_bytes < 1024:
        return f"{size_bytes} B"
    for unit, limit in (('KB', 1024 ** 2), ('MB', 1024 ** 3), ('GB', 1024 ** 4)):
        if size_bytes < limit:
            return f"{size_bytes / (limit / 1024):.1f} {unit}"
    return f"{size_bytes / 1024 ** 4:.1f} TB"


# A backup archive is the whole instance in one file, so it is legitimately far
# larger than MAX_CONTENT_LENGTH, which exists to bound a single *attachment*.
# Werkzeug offers no "unlimited" sentinel — assigning None to
# request.max_content_length makes it fall back to the config value again — so
# this is a ceiling high enough to be unlimited for any real instance while
# still bounding a runaway request.
NO_PRACTICAL_UPLOAD_LIMIT = 64 * 1024 ** 3  # 64 GB


def allow_large_upload():
    """Lift the per-request upload cap, for restore only.

    Must be called before anything touches request.form or request.files: the
    limit is enforced by the form parser, and by then it is too late. Exceeding
    it does not produce a tidy 413 either — the server stops reading the body
    part-way and the browser reports a connection reset, which is why the cap
    is lifted here rather than merely raised.
    """
    from flask import request

    request.max_content_length = NO_PRACTICAL_UPLOAD_LIMIT


# ── creating a record from inside a picker ─────────────────────────────────
#
# A form rendered with ?embedded=1 is running in the modal behind a "+" button
# on another form. Success there must not navigate: the page underneath still
# holds a half-filled work order. So instead of the usual redirect-to-detail it
# renders a page that hands the new record back to the opener, which inserts the
# option and selects it.

def is_embedded():
    """Whether this request is a create form running inside a picker modal."""
    from flask import request

    return request.args.get('embedded') == '1'


def embedded_created(kind, record_id, label, **extra):
    """The response a create route returns instead of redirecting."""
    from flask import render_template

    payload = {'kind': kind, 'id': record_id, 'label': label}
    payload.update(extra)
    return render_template('_created.html', payload=payload)
