import os

from flask import send_file, abort, redirect, url_for, flash, current_app
from flask_login import login_required

from app.attachments import bp
from app.extensions import db
from app.models.attachment import Attachment
from app.utils import validate_csrf, entity_upload_dir

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
    return send_file(file_path, download_name=att.original_filename, as_attachment=True)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    att = db.get_or_404(Attachment, id)
    entity_type, entity_id = att.entity_type, att.entity_id

    file_path = _attachment_path(att)
    if os.path.exists(file_path):
        os.remove(file_path)

    db.session.delete(att)
    db.session.commit()
    flash('Attachment deleted.', 'success')

    endpoint = ENTITY_ENDPOINTS.get(entity_type)
    if endpoint:
        return redirect(url_for(endpoint, id=entity_id))
    return redirect(url_for('main.dashboard'))
