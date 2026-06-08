import os
from flask import send_file, abort, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.attachments import bp
from app.extensions import db
from app.models.attachment import Attachment
from app.utils import validate_csrf
from flask import current_app


@bp.route('/<int:id>/download')
@login_required
def download(id):
    att = Attachment.query.get_or_404(id)
    file_path = os.path.join(
        current_app.config['UPLOAD_FOLDER'],
        att.entity_type,
        str(att.entity_id),
        att.stored_filename,
    )
    if not os.path.exists(file_path):
        abort(404)
    return send_file(file_path, download_name=att.original_filename, as_attachment=True)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    att = Attachment.query.get_or_404(id)
    entity_type = att.entity_type
    entity_id = att.entity_id

    file_path = os.path.join(
        current_app.config['UPLOAD_FOLDER'],
        att.entity_type,
        str(att.entity_id),
        att.stored_filename,
    )
    if os.path.exists(file_path):
        os.remove(file_path)

    db.session.delete(att)
    db.session.commit()
    flash('Attachment deleted.', 'success')

    # Redirect back to the originating entity detail page
    route_map = {
        'location': 'locations.detail',
        'asset': 'assets.detail',
        'work_order': 'work_orders.detail',
        'job_plan': 'job_plans.detail',
        'pm': 'pms.detail',
    }
    endpoint = route_map.get(entity_type)
    if endpoint:
        return redirect(url_for(endpoint, id=entity_id))
    return redirect(url_for('main.dashboard'))
