from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.locations import bp
from app.extensions import db
from app.models.location import Location
from app.models.attachment import Attachment
from app.utils import validate_csrf, allowed_file, save_attachment


@bp.route('/')
@login_required
def list():
    locations = Location.query.order_by(Location.name).all()
    return render_template('locations/list.html', locations=locations)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        validate_csrf()
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'error')
            return render_template('locations/form.html', location=None)
        if Location.query.filter_by(name=name).first():
            flash('A location with that name already exists.', 'error')
            return render_template('locations/form.html', location=None)

        location = Location(
            name=name,
            description=request.form.get('description', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
        )
        db.session.add(location)
        db.session.commit()
        flash('Location created.', 'success')
        return redirect(url_for('locations.detail', id=location.id))

    return render_template('locations/form.html', location=None)


@bp.route('/<int:id>')
@login_required
def detail(id):
    location = Location.query.get_or_404(id)
    attachments = Attachment.query.filter_by(entity_type='location', entity_id=id).order_by(Attachment.uploaded_at.desc()).all()
    return render_template('locations/detail.html', location=location, attachments=attachments)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    location = Location.query.get_or_404(id)
    if request.method == 'POST':
        validate_csrf()
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'error')
            return render_template('locations/form.html', location=location)
        existing = Location.query.filter_by(name=name).first()
        if existing and existing.id != id:
            flash('A location with that name already exists.', 'error')
            return render_template('locations/form.html', location=location)

        location.name = name
        location.description = request.form.get('description', '').strip() or None
        location.notes = request.form.get('notes', '').strip() or None
        db.session.commit()
        flash('Location updated.', 'success')
        return redirect(url_for('locations.detail', id=id))

    return render_template('locations/form.html', location=location)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    location = Location.query.get_or_404(id)
    db.session.delete(location)
    db.session.commit()
    flash('Location deleted.', 'success')
    return redirect(url_for('locations.list'))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    location = Location.query.get_or_404(id)
    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('locations.detail', id=id))
    if not allowed_file(file.filename):
        flash('File type not allowed.', 'error')
        return redirect(url_for('locations.detail', id=id))

    stored, original, size, mime = save_attachment(file, 'location', id)
    att = Attachment(
        entity_type='location', entity_id=id,
        stored_filename=stored, original_filename=original,
        file_size=size, mime_type=mime, uploaded_by=current_user.id,
    )
    db.session.add(att)
    db.session.commit()
    flash('File uploaded.', 'success')
    return redirect(url_for('locations.detail', id=id))
