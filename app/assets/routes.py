from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.assets import bp
from app.extensions import db
from app.models.asset import Asset, ASSET_CATEGORIES
from app.models.location import Location
from app.models.attachment import Attachment
from app.utils import validate_csrf, allowed_file, save_attachment
from datetime import date


def _parse_date(val):
    try:
        return date.fromisoformat(val) if val else None
    except ValueError:
        return None


@bp.route('/')
@login_required
def list():
    category = request.args.get('category', '')
    location_id = request.args.get('location_id', '')
    q = Asset.query
    if category:
        q = q.filter_by(category=category)
    if location_id:
        q = q.filter_by(location_id=int(location_id))
    assets = q.order_by(Asset.name).all()
    locations = Location.query.order_by(Location.name).all()
    return render_template('assets/list.html', assets=assets, locations=locations,
                           categories=ASSET_CATEGORIES, selected_category=category,
                           selected_location=location_id)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    locations = Location.query.order_by(Location.name).all()
    if request.method == 'POST':
        validate_csrf()
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'error')
            return render_template('assets/form.html', asset=None, locations=locations, categories=ASSET_CATEGORIES)

        asset = Asset(
            name=name,
            location_id=request.form.get('location_id') or None,
            category=request.form.get('category') or None,
            make=request.form.get('make', '').strip() or None,
            model=request.form.get('model', '').strip() or None,
            serial_number=request.form.get('serial_number', '').strip() or None,
            purchase_date=_parse_date(request.form.get('purchase_date')),
            install_date=_parse_date(request.form.get('install_date')),
            warranty_expiry=_parse_date(request.form.get('warranty_expiry')),
            notes=request.form.get('notes', '').strip() or None,
        )
        db.session.add(asset)
        db.session.commit()
        flash('Asset created.', 'success')
        return redirect(url_for('assets.detail', id=asset.id))

    return render_template('assets/form.html', asset=None, locations=locations, categories=ASSET_CATEGORIES)


@bp.route('/<int:id>')
@login_required
def detail(id):
    asset = Asset.query.get_or_404(id)
    attachments = Attachment.query.filter_by(entity_type='asset', entity_id=id).order_by(Attachment.uploaded_at.desc()).all()
    return render_template('assets/detail.html', asset=asset, attachments=attachments)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    asset = Asset.query.get_or_404(id)
    locations = Location.query.order_by(Location.name).all()
    if request.method == 'POST':
        validate_csrf()
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'error')
            return render_template('assets/form.html', asset=asset, locations=locations, categories=ASSET_CATEGORIES)

        asset.name = name
        asset.location_id = request.form.get('location_id') or None
        asset.category = request.form.get('category') or None
        asset.make = request.form.get('make', '').strip() or None
        asset.model = request.form.get('model', '').strip() or None
        asset.serial_number = request.form.get('serial_number', '').strip() or None
        asset.purchase_date = _parse_date(request.form.get('purchase_date'))
        asset.install_date = _parse_date(request.form.get('install_date'))
        asset.warranty_expiry = _parse_date(request.form.get('warranty_expiry'))
        asset.notes = request.form.get('notes', '').strip() or None
        db.session.commit()
        flash('Asset updated.', 'success')
        return redirect(url_for('assets.detail', id=id))

    return render_template('assets/form.html', asset=asset, locations=locations, categories=ASSET_CATEGORIES)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    asset = Asset.query.get_or_404(id)
    db.session.delete(asset)
    db.session.commit()
    flash('Asset deleted.', 'success')
    return redirect(url_for('assets.list'))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    Asset.query.get_or_404(id)
    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('assets.detail', id=id))
    if not allowed_file(file.filename):
        flash('File type not allowed.', 'error')
        return redirect(url_for('assets.detail', id=id))

    stored, original, size, mime = save_attachment(file, 'asset', id)
    att = Attachment(
        entity_type='asset', entity_id=id,
        stored_filename=stored, original_filename=original,
        file_size=size, mime_type=mime, uploaded_by=current_user.id,
    )
    db.session.add(att)
    db.session.commit()
    flash('File uploaded.', 'success')
    return redirect(url_for('assets.detail', id=id))
