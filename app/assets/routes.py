import os

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.assets import bp
from app.extensions import db
from app.models.asset import Asset, ASSET_CATEGORIES
from app.models.location import Location
from app.models.mixins import LIFECYCLE_STATUSES, STATUS_ACTIVE, STATUS_LABELS, STATUS_HELP
from app.models.attachment import Attachment
from app.models.asset_material import AssetMaterial
from app.services import (
    _match_existing_material, asset_delete_blockers, create_asset, hierarchy_ordered,
    selectable_locations,
)
from app.utils import (
    validate_csrf, purge_entity_attachments, store_uploads, named_uploads, is_image_file,
    entity_upload_dir, discard_thumbnail,
    parse_date, parse_int, choice, is_embedded, embedded_created,
)

ENTITY = 'asset'


def _parent_options(asset=None):
    """Assets that may legally become `asset`'s parent — not itself, not its own
    descendants (either would form a cycle)."""
    q = Asset.query.order_by(Asset.name)
    if asset is None:
        return q.all()
    excluded = {asset.id} | {node.id for node in asset.descendants}
    return [a for a in q.all() if a.id not in excluded]


def _form_context(asset=None):
    return dict(
        asset=asset,
        # Keep whatever the record already points at, even if it was since
        # retired, so editing an asset cannot silently move it.
        locations=selectable_locations(include_id=asset.location_id if asset else None),
        parents=_parent_options(asset),
        categories=ASSET_CATEGORIES,
        statuses=LIFECYCLE_STATUSES,
        status_labels=STATUS_LABELS,
        status_help=STATUS_HELP,
    )


def _read_form(asset=None):
    name = request.form.get('name', '').strip()
    parent_id = parse_int(request.form.get('parent_id'))
    status = choice(request.form.get('status'), LIFECYCLE_STATUSES, STATUS_ACTIVE)

    errors = []
    if not name:
        errors.append('Name is required.')

    parent = None
    if parent_id is not None:
        parent = db.session.get(Asset, parent_id)
        if parent is None:
            errors.append('That parent asset no longer exists.')
        elif asset is not None and asset.would_create_cycle(parent):
            errors.append(
                f"'{parent.name}' sits beneath this asset, so it cannot also be its parent."
            )

    # Checked here so a bad file stops the submission before anything is
    # written, rather than leaving a new asset behind with no photo.
    image = request.files.get('image')
    if image and image.filename and not is_image_file(image.filename):
        errors.append('That file is not an image. Use a PNG, JPG, GIF or WEBP.')

    return name, parent, status, errors


def _apply_common_fields(asset):
    asset.category = request.form.get('category') or None
    asset.make = request.form.get('make', '').strip() or None
    asset.model = request.form.get('model', '').strip() or None
    asset.serial_number = request.form.get('serial_number', '').strip() or None
    asset.purchase_date = parse_date(request.form.get('purchase_date'))
    asset.install_date = parse_date(request.form.get('install_date'))
    asset.warranty_expiry = parse_date(request.form.get('warranty_expiry'))
    asset.notes = request.form.get('notes', '').strip() or None


@bp.route('/')
@login_required
def index():
    category = request.args.get('category', '')
    location_id = parse_int(request.args.get('location_id'))
    show_all = request.args.get('show', 'active') == 'all'

    q = Asset.query
    if category:
        q = q.filter_by(category=category)
    if location_id is not None:
        q = q.filter_by(location_id=location_id)
    if not show_all:
        q = q.filter(Asset.status == STATUS_ACTIVE)

    rows = hierarchy_ordered(q.order_by(Asset.name).all())
    return render_template(
        'assets/list.html', rows=rows,
        locations=Location.query.order_by(Location.name).all(),
        categories=ASSET_CATEGORIES, selected_category=category,
        selected_location=str(location_id) if location_id is not None else '',
        show_all=show_all,
    )


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        validate_csrf()
        name, parent, status, errors = _read_form()
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('assets/form.html', **_form_context())

        asset = create_asset(
            name=name,
            location_id=parse_int(request.form.get('location_id')),
            parent_id=parent.id if parent else None,
            status=status,
        )
        _apply_common_fields(asset)
        # The photo is filed under the asset's id, so it can only be stored once
        # the asset exists — create_asset() has already committed.
        _apply_photo(asset)
        db.session.commit()
        if is_embedded():
            return embedded_created(
                'asset', asset.id, f'{asset.name} ({asset.asset_number})',
                location_id=asset.location_id)
        flash(f'Asset {asset.asset_number} created.', 'success')
        return redirect(url_for('assets.detail', id=asset.id))

    return render_template('assets/form.html', **_form_context())


@bp.route('/<int:id>')
@login_required
def detail(id):
    asset = db.get_or_404(Asset, id)
    attachments = (
        Attachment.query
        .filter_by(entity_type=ENTITY, entity_id=id)
        .order_by(Attachment.uploaded_at.desc())
        .all()
    )
    return render_template(
        'assets/detail.html', asset=asset, attachments=attachments,
        blockers=asset_delete_blockers(asset), status_help=STATUS_HELP,
    )


@bp.route('/<int:id>/summary')
@login_required
def summary(id):
    """Small JSON payload the work order form fetches to inherit the location."""
    asset = db.get_or_404(Asset, id)
    return {
        'id': asset.id,
        'name': asset.name,
        'status': asset.status,
        'location_id': asset.location_id,
        'location_name': asset.location.name if asset.location else None,
        'location_path': asset.location.path_label if asset.location else None,
    }


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    asset = db.get_or_404(Asset, id)
    if request.method == 'POST':
        validate_csrf()
        name, parent, status, errors = _read_form(asset)
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('assets/form.html', **_form_context(asset))

        asset.name = name
        asset.location_id = parse_int(request.form.get('location_id'))
        asset.parent_id = parent.id if parent else None
        asset.status = status
        _apply_common_fields(asset)
        if not _apply_photo(asset):
            db.session.rollback()
            return render_template('assets/form.html', **_form_context(asset))
        db.session.commit()
        flash('Asset updated.', 'success')
        return redirect(url_for('assets.detail', id=id))

    return render_template('assets/form.html', **_form_context(asset))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    asset = db.get_or_404(Asset, id)

    blockers = asset_delete_blockers(asset)
    if blockers:
        flash(
            f"'{asset.name}' cannot be deleted — it still has "
            f"{', '.join(blockers)}. Set its status to Decommissioned instead to "
            "retire it while keeping the history.",
            'error',
        )
        return redirect(url_for('assets.detail', id=id))

    purge_entity_attachments(ENTITY, id)
    db.session.delete(asset)
    db.session.commit()
    flash('Asset deleted.', 'success')
    return redirect(url_for('assets.index'))


def _discard_image(asset):
    """Remove the current photo's attachment row and file, if any."""
    old = asset.image
    asset.image_attachment_id = None
    db.session.flush()
    if old is None:
        return
    path = os.path.join(entity_upload_dir(old.entity_type, old.entity_id), old.stored_filename)
    if os.path.exists(path):
        os.remove(path)
    discard_thumbnail(old.id)
    db.session.delete(old)


def _apply_photo(asset):
    """Apply the photo controls on the edit form.

    Ticking "remove" clears it; choosing a file replaces whatever was there.
    Both happen inside the edit transaction, so a failed save changes nothing.
    Returns False if the upload was rejected.
    """
    if request.form.get('remove_image'):
        _discard_image(asset)
        return True

    file = request.files.get('image')
    if not file or not file.filename:
        # No new file: just let a caption edit through, if there is a photo.
        caption = request.form.get('image_caption')
        if caption is not None and asset.image is not None:
            asset.image.display_name = caption.strip() or None
        return True

    if not is_image_file(file.filename):
        flash('That file is not an image. Use a PNG, JPG, GIF or WEBP.', 'error')
        return False

    _discard_image(asset)
    saved, errors = store_uploads(
        ENTITY, asset.id,
        [(file, request.form.get('image_caption', '').strip() or None)],
        current_user.id,
    )
    for message in errors:
        flash(message, 'error')
    if saved:
        db.session.flush()          # assign the new attachment an id
        asset.image_attachment_id = saved[0].id
    return True


# ── Materials ──────────────────────────────────────────────────────────────
#
# Usually these accumulate from completed work orders, but a part number you
# already know is worth recording without inventing a job to hang it on.

@bp.route('/<int:id>/materials', methods=['POST'])
@login_required
def add_material(id):
    validate_csrf()
    asset = db.get_or_404(Asset, id)

    description = request.form.get('description', '').strip()
    part_number = request.form.get('part_number', '').strip() or None
    quantity = request.form.get('quantity', '').strip() or None

    if not description:
        flash('Give the material a description.', 'error')
        return redirect(url_for('assets.detail', id=id))

    # Same matching the work order roll-up uses, so adding something already on
    # the list fills in what is missing instead of creating a near-duplicate.
    existing = AssetMaterial.query.filter_by(asset_id=id).all()
    match = _match_existing_material(existing, description, part_number)
    if match is not None:
        match.part_number = part_number or match.part_number
        match.quantity = quantity or match.quantity
        db.session.commit()
        flash(f"'{match.description}' was already listed; updated it instead.", 'info')
        return redirect(url_for('assets.detail', id=id))

    db.session.add(AssetMaterial(
        asset_id=id, description=description, part_number=part_number,
        quantity=quantity,
        # Nothing has used it yet; a completed work order will start the count.
        times_used=0,
    ))
    db.session.commit()
    flash('Material added.', 'success')
    return redirect(url_for('assets.detail', id=id))


@bp.route('/<int:id>/materials/<int:material_id>/edit', methods=['POST'])
@login_required
def edit_material(id, material_id):
    validate_csrf()
    db.get_or_404(Asset, id)
    material = db.session.get(AssetMaterial, material_id)
    if material is None or material.asset_id != id:
        flash('That material could not be found.', 'error')
        return redirect(url_for('assets.detail', id=id))

    description = request.form.get('description', '').strip()
    if not description:
        flash('The description cannot be empty.', 'error')
        return redirect(url_for('assets.detail', id=id))

    material.description = description
    material.part_number = request.form.get('part_number', '').strip() or None
    material.quantity = request.form.get('quantity', '').strip() or None
    db.session.commit()
    flash('Material updated.', 'success')
    return redirect(url_for('assets.detail', id=id))


@bp.route('/<int:id>/materials/<int:material_id>/delete', methods=['POST'])
@login_required
def delete_material(id, material_id):
    validate_csrf()
    db.get_or_404(Asset, id)
    material = db.session.get(AssetMaterial, material_id)
    if material is None or material.asset_id != id:
        flash('That material could not be found.', 'error')
        return redirect(url_for('assets.detail', id=id))

    db.session.delete(material)
    db.session.commit()
    flash('Material removed.', 'success')
    return redirect(url_for('assets.detail', id=id))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    db.get_or_404(Asset, id)
    rows = named_uploads(request.files.getlist('file'),
                         request.form.get('display_name', '').strip() or None)
    if not rows:
        flash('No file selected.', 'error')
        return redirect(url_for('assets.detail', id=id))

    saved, errors = store_uploads(ENTITY, id, rows, current_user.id)
    for message in errors:
        flash(message, 'error')
    if saved:
        db.session.commit()
        count = len(saved)
        flash(f"{count} file{'' if count == 1 else 's'} uploaded.", 'success')
    return redirect(url_for('assets.detail', id=id))
