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


def purge_entity_attachments(entity_type, entity_id):
    """Delete an entity's attachment rows and its upload directory.

    Attachments are polymorphic (entity_type + entity_id, no foreign key), so
    nothing in the database cleans them up when the parent row goes away. Every
    entity delete route must call this or the rows and files are orphaned.
    """
    from app.extensions import db
    from app.models.attachment import Attachment

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
    if size_bytes is None:
        return '—'
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
