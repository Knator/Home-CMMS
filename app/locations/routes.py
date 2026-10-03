from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.locations import bp
from app.extensions import db
from app.models.location import Location
from app.models.mixins import LIFECYCLE_STATUSES, STATUS_ACTIVE, STATUS_LABELS, STATUS_HELP
from app.models.attachment import Attachment
from app.navigation import carried_list_args, neighbours, page_number, paginate_tree
from app.search import (
    SearchTooSlow, compile_pattern, like_clause, regex_filter, too_slow_message,
)

from app.models.work_order import WorkOrder
from app.services import (
    create_location, location_delete_blockers, hierarchy_ordered, sibling_name_taken,
)
from app.utils import (
    validate_csrf, purge_entity_attachments, store_uploads, named_uploads,
    is_embedded, embedded_created,
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


def _filtered_locations(args):
    """The location list exactly as the index page builds it, hierarchy pass
    included, so the sequence is the depth-first order on screen. Returns
    `(rows, problem)` where each row is `(location, depth)`.
    """
    show_all = args.get('show', 'active') == 'all'
    search = args.get('q', '').strip()
    use_regex = bool(args.get('regex'))

    q = Location.query
    if not show_all:
        q = q.filter(Location.status == STATUS_ACTIVE)

    pattern = None
    if search and use_regex:
        pattern, regex_error = compile_pattern(search)
        if regex_error:
            return [], ('invalid', regex_error)
    elif search:
        q = q.filter(like_clause(
            search, Location.name, Location.description, Location.notes))

    matched = q.order_by(Location.name).all()
    if pattern is not None:
        try:
            matched = regex_filter(
                pattern, matched,
                lambda loc: (loc.name, loc.description, loc.notes))
        except SearchTooSlow:
            return [], ('slow', None)

    # Filtered first, arranged second: hierarchy_ordered promotes a match whose
    # parent did not match, so searching for a child still finds it rather than
    # hiding it under a branch that was filtered away.
    return hierarchy_ordered(matched), None


@bp.route('/')
@login_required
def index():
    rows, problem = _filtered_locations(request.args)
    page = paginate_tree(rows, page_number(request.args))
    any_records = bool(page.items) or Location.query.first() is not None
    if problem:
        kind, detail_text = problem
        if kind == 'slow':
            flash(too_slow_message().capitalize(), 'error')
        else:
            flash(f'That is not a valid regular expression: {detail_text}', 'error')

    return render_template('locations/list.html', rows=page.items, page=page,
                           show_all=request.args.get('show', 'active') == 'all',
                           search=request.args.get('q', '').strip(),
                           use_regex=bool(request.args.get('regex')), any_records=any_records)


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

        location = create_location(
            name=name,
            parent_id=parent.id if parent else None,
            status=status,
            description=request.form.get('description', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
        )
        if is_embedded():
            return embedded_created(
                'location', location.id,
                f'{location.name} ({location.location_number})')
        flash(f'Location {location.location_number} created.', 'success')
        # The create form posted to its own URL, so the list's filters
        # came with it; Back from the new record returns to that list.
        return redirect(url_for('locations.detail', id=location.id,
                                **carried_list_args(request.args)))

    return render_template('locations/form.html', **_form_context())


@bp.route('/<int:id>')
@login_required
def detail(id):
    previous_id, next_id, position, total = neighbours(
        [row.id for row, _depth in _filtered_locations(request.args)[0]], id)
    previous = db.session.get(Location, previous_id) if previous_id else None
    following = db.session.get(Location, next_id) if next_id else None
    location = db.get_or_404(Location, id)
    attachments = (
        Attachment.query
        .filter_by(entity_type=ENTITY, entity_id=id)
        .order_by(Attachment.uploaded_at.desc())
        .all()
    )
    work_orders = (
        location.work_orders
        .filter(WorkOrder.archived_at.is_(None))
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
        previous_location=previous, next_location=following,
        position=position, total=total,
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
        # Back to the record with the list's filters still attached — the edit
        # form posted to its own URL, query string and all, so they are here.
        return redirect(url_for('locations.detail', id=id,
                                **carried_list_args(request.args)))

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
        return redirect(url_for('locations.detail', id=id, **carried_list_args(request.args)))

    purge_entity_attachments(ENTITY, id)
    db.session.delete(location)
    db.session.commit()
    flash('Location deleted.', 'success')
    return redirect(url_for('locations.index', **carried_list_args(request.args)))


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
