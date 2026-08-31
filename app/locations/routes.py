from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.locations import bp
from app.extensions import db
from app.models.location import Location
from app.models.mixins import LIFECYCLE_STATUSES, STATUS_ACTIVE, STATUS_LABELS, STATUS_HELP
from app.models.attachment import Attachment
from app.models.work_order import WorkOrder
from app.services import location_delete_blockers, hierarchy_ordered, sibling_name_taken
from app.utils import (
    validate_csrf, purge_entity_attachments, store_uploads, named_uploads,
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

    parent = None
    if parent_id is not None:
        parent = db.session.get(Location, parent_id)
        if parent is None:
            errors.append('That parent location no longer exists.')
        elif location is not None and location.would_create_cycle(parent):
            errors.append(
                f"'{parent.name}' sits beneath this location, so it cannot also be its parent."
            )

    # Names only have to be unique among siblings, so this is checked against
    # the parent the form is submitting, not globally.
    if name and not errors and sibling_name_taken(location, name, parent.id if parent else None):
        where = f"under '{parent.name}'" if parent else 'at the top level'
        errors.append(f"A location called '{name}' already exists {where}.")

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
    work_orders = (
        location.work_orders
        .order_by(WorkOrder.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template(
        'locations/detail.html',
        location=location,
        attachments=attachments,
        work_orders=work_orders,
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
    rows = named_uploads(request.files.getlist('file'),
                         request.form.get('display_name', '').strip() or None)
    if not rows:
        flash('No file selected.', 'error')
        return redirect(url_for('locations.detail', id=id))

    saved, errors = store_uploads(ENTITY, id, rows, current_user.id)
    for message in errors:
        flash(message, 'error')
    if saved:
        db.session.commit()
        count = len(saved)
        flash(f"{count} file{'' if count == 1 else 's'} uploaded.", 'success')
    return redirect(url_for('locations.detail', id=id))
