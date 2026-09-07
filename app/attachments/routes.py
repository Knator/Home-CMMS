import os

from flask import send_file, abort, redirect, url_for, flash, request, current_app
from flask_login import login_required

from app.attachments import bp
from app.extensions import db
from app.models.attachment import Attachment
from app.utils import (
    validate_csrf, entity_upload_dir, is_image_file, build_thumbnail, discard_thumbnail,
    is_viewable_file, is_text_view_file,
    THUMBNAIL_FALLBACK_MAX_BYTES,
)

# Where to send the user after deleting a file, per owning entity.
ENTITY_ENDPOINTS = {
    'location': 'locations.detail',
    'asset': 'assets.detail',
    'work_order': 'work_orders.detail',
    'job_plan': 'job_plans.detail',
    'pm': 'pms.detail',
}


def _attachment_path(att):
    """Resolve an attachment's path, refusing anything outside UPLOAD_FOLDER.

    Stored names are generated server-side, so this is belt-and-braces against a
    a tampered database row escaping the upload directory.
    """
    root = os.path.abspath(current_app.config['UPLOAD_FOLDER'])
    path = os.path.abspath(os.path.join(
        entity_upload_dir(att.entity_type, att.entity_id), att.stored_filename
    ))
    if os.path.commonpath([root, path]) != root:
        abort(404)
    return path


@bp.route('/<int:id>/download')
@login_required
def download(id):
    att = db.get_or_404(Attachment, id)
    file_path = _attachment_path(att)
    if not os.path.exists(file_path):
        abort(404)
    # Saves as the friendly name when one is set, keeping the real extension.
    return send_file(file_path, download_name=att.download_name, as_attachment=True)


def _back_to_entity(entity_type, entity_id):
    """Send the user back to the detail page of whatever owns the attachment."""
    endpoint = ENTITY_ENDPOINTS.get(entity_type)
    if endpoint:
        return redirect(url_for(endpoint, id=entity_id))
    return redirect(url_for('main.dashboard'))


@bp.route('/<int:id>/inline')
@login_required
def inline(id):
    """Serve a file for the browser to render rather than save.

    Restricted to VIEWABLE_EXTENSIONS — images, PDF, the video and audio formats
    browsers play, and text — and sent with nosniff, so a file that is not
    really what its extension claims cannot be sniffed into something that
    executes. Text-ish formats are forced to text/plain: served as their own
    type, XML in particular can carry a stylesheet that runs script in this
    origin, and as plain text it cannot.
    """
    att = db.get_or_404(Attachment, id)
    if not is_viewable_file(att.original_filename):
        abort(404)

    file_path = _attachment_path(att)
    if not os.path.exists(file_path):
        abort(404)

    kwargs = {'as_attachment': False}
    if is_text_view_file(att.original_filename):
        kwargs['mimetype'] = 'text/plain'
    response = send_file(file_path, **kwargs)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.route('/<int:id>/thumbnail')
@login_required
def thumbnail(id):
    """Small cached preview of an image attachment.

    Falls back to the original if a thumbnail cannot be produced (Pillow absent,
    or an image this build cannot decode) so the page still shows something.
    """
    att = db.get_or_404(Attachment, id)
    if not is_image_file(att.original_filename):
        abort(404)

    source = _attachment_path(att)
    if not os.path.exists(source):
        abort(404)

    thumb = build_thumbnail(source, att.id)
    if thumb is None:
        # No thumbnail could be made. Serving the original is only acceptable
        # when it is small; a large one would stall the page it appears on.
        if os.path.getsize(source) > THUMBNAIL_FALLBACK_MAX_BYTES:
            abort(404)
        thumb = source

    response = send_file(thumb, as_attachment=False)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Immutable: a replaced file is a new attachment id, so this can be cached hard.
    response.headers['Cache-Control'] = 'private, max-age=604800'
    return response


@bp.route('/<int:id>/rename', methods=['POST'])
@login_required
def rename(id):
    """Set or clear an attachment's friendly name. The stored file is untouched."""
    validate_csrf()
    att = db.get_or_404(Attachment, id)
    display_name = request.form.get('display_name', '').strip()

    if len(display_name) > 255:
        flash('That name is too long (255 characters maximum).', 'error')
        return _back_to_entity(att.entity_type, att.entity_id)

    att.display_name = display_name or None
    db.session.commit()
    flash('Name updated.' if display_name else 'Name cleared.', 'success')
    return _back_to_entity(att.entity_type, att.entity_id)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    att = db.get_or_404(Attachment, id)
    entity_type, entity_id = att.entity_type, att.entity_id

    file_path = _attachment_path(att)
    if os.path.exists(file_path):
        os.remove(file_path)
    discard_thumbnail(att.id)

    db.session.delete(att)
    db.session.commit()
    flash('Attachment deleted.', 'success')

    return _back_to_entity(entity_type, entity_id)
