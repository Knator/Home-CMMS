from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.locations import bp
from app.extensions import db
from app.models.location import Location
from app.models.mixins import LIFECYCLE_STATUSES, STATUS_ACTIVE, STATUS_LABELS, STATUS_HELP
from app.models.attachment import Attachment
from app.services import location_delete_blockers, hierarchy_ordered
from app.utils import (
    validate_csrf, allowed_file, save_attachment, purge_entity_attachments,
    parse_int, choice,
)

ENTITY = 'location'


def _parent_options(location=None):
    """Every location that may legally become `location`'s parent.

    Excludes itself and everything beneath it, since either would form a cycle.
    """
    q = Location.query.order_by(Location.name)
    if location is None:
        return q.all()
    excluded = {location.id} | {node.id for node in location.descendants}
    return [loc for loc in q.all() if loc.id not in excluded]


def _read_form(location=None):
    name = request.form.get('name', '').strip()
    parent_id = parse_int(request.form.get('parent_id'))
    status = choice(request.form.get('status'), LIFECYCLE_STATUSES, STATUS_ACTIVE)

    errors = []
    if not name:
        errors.append('Name is required.')
    else:
        clash = Location.query.filter_by(name=name).first()
        if clash and (location is None or clash.id != location.id):
            errors.append('A location with that name already exists.')

    parent = None
    if parent_id is not None:
        parent = db.session.get(Location, parent_id)
        if parent is None:
            errors.append('That parent location no longer exists.')
        elif location is not None and location.would_create_cycle(parent):
            errors.append(
                f"'{parent.name}' sits beneath this location, so it cannot also be its parent."
            )
    return name, parent, status, errors


def _form_context(location=None):
    return dict(
        location=location,
        parents=_parent_options(location),
        statuses=LIFECYCLE_STATUSES,
        status_labels=STATUS_LABELS,
        status_help=STATUS_HELP,
    )


@bp.route('/')
@login_required
def index():
    show_all = request.args.get('show', 'active') == 'all'
    q = Location.query
    if not show_all:
        q = q.filter(Location.status == STATUS_ACTIVE)
    rows = hierarchy_ordered(q.order_by(Location.name).all())
    return render_template('locations/list.html', rows=rows, show_all=show_all)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        validate_csrf()
        name, parent, status, errors = _read_form()
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('locations/form.html', **_form_context())

        location = Location(
            name=name,
            parent_id=parent.id if parent else None,
            status=status,
            description=request.form.get('description', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
        )
        db.session.add(location)
        db.session.commit()
        flash('Location created.', 'success')
        return redirect(url_for('locations.detail', id=location.id))

    return render_template('locations/form.html', **_form_context())


@bp.route('/<int:id>')
@login_required
def detail(id):
    location = db.get_or_404(Location, id)
    attachments = (
        Attachment.query
        .filter_by(entity_type=ENTITY, entity_id=id)
        .order_by(Attachment.uploaded_at.desc())
        .all()
    )
    return render_template(
        'locations/detail.html',
        location=location,
        attachments=attachments,
        blockers=location_delete_blockers(location),
        status_help=STATUS_HELP,
    )


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    location = db.get_or_404(Location, id)
    if request.method == 'POST':
        validate_csrf()
        name, parent, status, errors = _read_form(location)
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('locations/form.html', **_form_context(location))

        location.name = name
        location.parent_id = parent.id if parent else None
        location.status = status
        location.description = request.form.get('description', '').strip() or None
        location.notes = request.form.get('notes', '').strip() or None
        db.session.commit()
        flash('Location updated.', 'success')
        return redirect(url_for('locations.detail', id=id))

    return render_template('locations/form.html', **_form_context(location))


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    location = db.get_or_404(Location, id)

    blockers = location_delete_blockers(location)
    if blockers:
        flash(
            f"'{location.name}' cannot be deleted — it still has "
            f"{', '.join(blockers)}. Set its status to Decommissioned instead to "
            "retire it while keeping the history.",
            'error',
        )
        return redirect(url_for('locations.detail', id=id))

    purge_entity_attachments(ENTITY, id)
    db.session.delete(location)
    db.session.commit()
    flash('Location deleted.', 'success')
    return redirect(url_for('locations.index'))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    db.get_or_404(Location, id)
    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('locations.detail', id=id))
    if not allowed_file(file.filename):
        flash('File type not allowed.', 'error')
        return redirect(url_for('locations.detail', id=id))

    stored, original, size, mime = save_attachment(file, ENTITY, id)
    att = Attachment(
        entity_type=ENTITY, entity_id=id,
        stored_filename=stored, original_filename=original,
        file_size=size, mime_type=mime, uploaded_by=current_user.id,
    )
    db.session.add(att)
    db.session.commit()
    flash('File uploaded.', 'success')
    return redirect(url_for('locations.detail', id=id))
