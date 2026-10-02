from datetime import date

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.work_orders import bp
from app.extensions import db
from app.models.work_order import (
    WorkOrder, WO_STATUSES, WO_PRIORITIES, WO_TYPES,
)

# How the list treats archived work. Its own filter box rather than a value in
# the status list, because archiving is orthogonal to outcome: a work order is
# completed or cancelled *and* archived or not, the way Maximo pairs a status
# with its history flag.
ARCHIVE_FILTERS = ('hide', 'show', 'only')
from app.models.job_plan import JobPlan
from app.models.user import User
from app.models.attachment import Attachment
from app.models.mixins import ITEM_MATERIAL, ITEM_TOOL
from app.models.work_order_item import WorkOrderItem
from app.services import (
    archive_work_order, copy_job_plan_items, create_work_order, NotArchivable,
    record_materials_on_asset, related_attachments, selectable_assets,
    selectable_locations, sync_pm_schedule,
)
from app.search import (
    SearchTooSlow, compile_pattern, like_clause, regex_filter, too_slow_message,
)
from app.settings import archived_deletion_allowed
from app.utils import (
    validate_csrf, purge_entity_attachments, store_uploads, named_uploads, upload_rows_from_form,
    parse_date, parse_int, choice,
)

ENTITY = 'work_order'


def _refuse_if_archived(wo):
    """Archived work orders are immutable. Returns a redirect, or None.

    Every write path consults this — edit, delete, attachments — rather than
    relying on the buttons being hidden, because a hidden button is a UI
    convenience and not a rule.
    """
    if not wo.is_archived:
        return None
    flash(f'{wo.wo_number} is archived and can no longer be changed.', 'error')
    return redirect(url_for('work_orders.detail', id=wo.id))


def _resolve_completed_date(status, current=None):
    """Work out the completion date from the form.

    A missing field leaves the existing value alone (so a POST that does not
    carry the input cannot wipe history); a present-but-empty field clears it.
    Marking a work order completed with no date falls back to today.
    """
    raw = request.form.get('completed_date')
    completed = parse_date(raw) if raw is not None else current
    if status == 'completed' and not completed:
        completed = date.today()
    return completed


MAX_ITEMS = 200


def _save_items(work_order):
    """Replace the work order's materials and tools from the submitted form.

    Deleted through the session rather than a bulk query so the delete-orphan
    cascade and the identity map stay in step.
    """
    for row in work_order.items.all():
        db.session.delete(row)
    db.session.flush()

    for kind, prefix in ((ITEM_MATERIAL, 'material'), (ITEM_TOOL, 'tool')):
        count = parse_int(request.form.get(f'{prefix}_count'), minimum=0) or 0
        sequence = 1
        for i in range(min(count, MAX_ITEMS)):
            description = request.form.get(f'{prefix}_{i}_description', '').strip()
            if not description:
                continue
            db.session.add(WorkOrderItem(
                work_order_id=work_order.id,
                kind=kind,
                sequence=sequence,
                description=description,
                quantity=request.form.get(f'{prefix}_{i}_quantity', '').strip() or None,
                part_number=request.form.get(f'{prefix}_{i}_part_number', '').strip() or None,
            ))
            sequence += 1


def _store_form_uploads(work_order_id, commit=True):
    """Persist any files attached on the create/edit form."""
    rows = upload_rows_from_form()
    if not rows:
        return
    saved, errors = store_uploads(ENTITY, work_order_id, rows, current_user.id)
    for message in errors:
        flash(message, 'error')
    if saved and commit:
        db.session.commit()
    if saved:
        count = len(saved)
        flash(f"{count} file{'' if count == 1 else 's'} attached.", 'success')


def _form_options(wo=None):
    """Pickers offer Active assets and locations only.

    A record that already points at something since retired keeps it listed, so
    editing an old work order cannot silently blank the field on save.
    """
    return dict(
        assets=selectable_assets(include_id=wo.asset_id if wo else None),
        locations=selectable_locations(include_id=wo.location_id if wo else None),
        job_plans=JobPlan.query.order_by(JobPlan.name).all(),
        # Sorted by what the list actually shows. Ordering by username would
        # look arbitrary once display names differ from it.
        users=sorted(User.query.filter_by(is_active=True).all(),
                     key=lambda u: u.label.lower()),
        statuses=WO_STATUSES, priorities=WO_PRIORITIES, wo_types=WO_TYPES,
    )


def _chosen(param, allowed, args=None):
    """The values picked for one filter, ignoring anything not in the vocabulary.

    A filter with nothing ticked means "no opinion", not "match nothing" — an
    empty list is how the filter says it should not narrow anything.

    `args` lets the detail page pass the filters it was handed, rather than this
    reaching into the live request for a list it is not rendering.
    """
    source = request.args if args is None else args
    return [value for value in source.getlist(param) if value in allowed]


def _filtered_work_orders(args):
    """The work order list exactly as the index page builds it.

    Shared with the detail page, which needs the same sequence to work out what
    the previous and next records are — computing it twice would let the arrows
    drift from the list the moment either filter changed.

    Returns `(work_orders, problem)`. Reporting is left to the caller: the list
    page flashes, while the detail page only wants neighbours and must not
    repeat the list page's error messages on an unrelated screen.
    """
    statuses = _chosen('status', WO_STATUSES, args)
    types = _chosen('type', WO_TYPES, args)
    priorities = _chosen('priority', WO_PRIORITIES, args)
    search = args.get('q', '').strip()
    use_regex = bool(args.get('regex'))

    archived = args.get('archived', '')
    if archived not in ARCHIVE_FILTERS:
        archived = 'hide'

    q = WorkOrder.query
    if statuses:
        q = q.filter(WorkOrder.status.in_(statuses))
    if types:
        q = q.filter(WorkOrder.wo_type.in_(types))
    if priorities:
        q = q.filter(WorkOrder.priority.in_(priorities))
    # A plain search runs in SQL; a regular expression cannot, because SQLite
    # ships no REGEXP implementation. Matching in Python instead keeps it to one
    # readable path and lets a bad pattern be reported rather than raised — and
    # the rows have already been narrowed by every other filter by then.
    pattern = None
    if search and use_regex:
        pattern, regex_error = compile_pattern(search)
        if regex_error:
            return [], ('invalid', regex_error)
    elif search:
        q = q.filter(like_clause(
            search, WorkOrder.title, WorkOrder.description, WorkOrder.notes))

    # Independent of status: archived work is history, not a working list, so it
    # is out of the way by default and stays that way even when you filter for
    # completed work.
    if archived == 'hide':
        q = q.filter(WorkOrder.archived_at.is_(None))
    elif archived == 'only':
        q = q.filter(WorkOrder.archived_at.isnot(None))

    work_orders = q.order_by(WorkOrder.created_at.desc()).all()
    if pattern is not None:
        try:
            work_orders = regex_filter(
                pattern, work_orders,
                lambda wo: (wo.title, wo.description, wo.notes))
        except SearchTooSlow:
            return [], ('slow', None)
    return work_orders, None


@bp.route('/')
@login_required
def index():
    work_orders, problem = _filtered_work_orders(request.args)

    # Reported here rather than in the helper, which the detail page also uses
    # and which must not flash a list page's errors onto another screen.
    if problem:
        kind, detail_text = problem
        if kind == 'slow':
            # Not a syntax error, so it must not be reported as one.
            flash(too_slow_message().capitalize(), 'error')
        else:
            flash(f'That is not a valid regular expression: {detail_text}', 'error')

    archived = request.args.get('archived', '')
    if archived not in ARCHIVE_FILTERS:
        archived = 'hide'

    return render_template(
        'work_orders/list.html',
        work_orders=work_orders,
        statuses=WO_STATUSES, priorities=WO_PRIORITIES, wo_types=WO_TYPES,
        selected_statuses=_chosen('status', WO_STATUSES),
        selected_types=_chosen('type', WO_TYPES),
        selected_priorities=_chosen('priority', WO_PRIORITIES),
        search=request.args.get('q', '').strip(),
        use_regex=bool(request.args.get('regex')),
        archive_filters=ARCHIVE_FILTERS, selected_archived=archived,
        today=date.today(),
        # Carried onto every row link, so opening a work order and coming back
        # lands on the same filtered list rather than an unfiltered one.
        list_args=request.args.to_dict(flat=False),
    )


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    options = _form_options()

    if request.method == 'POST':
        validate_csrf()
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required.', 'error')
            return render_template('work_orders/form.html', wo=None, **options)

        status = choice(request.form.get('status'), WO_STATUSES, 'open')
        completed_date = _resolve_completed_date(status)
        # Same rule as the edit form: a completion date says the job is done, so
        # the status follows. No transition test is needed here — a work order
        # being created has no earlier date to have kept.
        if completed_date and status != 'completed':
            status = 'completed'
            flash('Marked completed, because a completion date was entered.', 'info')

        wo = create_work_order(
            title=title,
            wo_type=choice(request.form.get('wo_type'), WO_TYPES, 'unplanned'),
            status=status,
            priority=choice(request.form.get('priority'), WO_PRIORITIES, 'medium'),
            asset_id=parse_int(request.form.get('asset_id')),
            location_id=parse_int(request.form.get('location_id')),
            job_plan_id=parse_int(request.form.get('job_plan_id')),
            assigned_to=parse_int(request.form.get('assigned_to')),
            due_date=parse_date(request.form.get('due_date')),
            overdue_grace_days=parse_int(request.form.get('overdue_grace_days'), minimum=0) or 0,
            completed_date=completed_date,
            description=request.form.get('description', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
            created_by=current_user.id,
        )
        # Attachments are filed under the work order's id, so they can only be
        # stored once it exists — create_work_order() has already committed.
        _store_form_uploads(wo.id)
        # Seed from the job plan first, so form rows override rather than
        # duplicate them.
        if request.form.get('material_count') or request.form.get('tool_count'):
            _save_items(wo)
        else:
            copy_job_plan_items(wo)
        if wo.status == 'completed':
            record_materials_on_asset(wo)
        db.session.commit()
        if sync_pm_schedule(wo):
            db.session.commit()
        flash(f'Work order {wo.wo_number} created.', 'success')
        return redirect(url_for('work_orders.detail', id=wo.id))

    return render_template('work_orders/form.html', wo=None, **options)


def _neighbours(work_order, args):
    """The records either side of this one in the list it was opened from.

    The query string the list page attached to the row link is replayed here, so
    the arrows walk the filtered sequence rather than every work order. Nothing
    is stored: the position lives in the URL, which means it survives a reload,
    a bookmark and a shared link.

    Returns `(previous, next, position, total)` — all None when this record is
    not in the list at all, which happens when the filters exclude it (opening
    an archived work order from a search that hides archived, say). Offering
    arrows into a sequence the record does not belong to would be worse than
    offering none.
    """
    work_orders, problem = _filtered_work_orders(args)
    if problem:
        return None, None, None, None

    ids = [row.id for row in work_orders]
    try:
        at = ids.index(work_order.id)
    except ValueError:
        return None, None, None, None

    previous = work_orders[at - 1] if at > 0 else None
    following = work_orders[at + 1] if at + 1 < len(work_orders) else None
    return previous, following, at + 1, len(work_orders)


@bp.route('/<int:id>')
@login_required
def detail(id):
    wo = db.get_or_404(WorkOrder, id)
    previous, following, position, total = _neighbours(wo, request.args)
    attachments = (
        Attachment.query
        .filter_by(entity_type=ENTITY, entity_id=id)
        .order_by(Attachment.uploaded_at.desc())
        .all()
    )
    tasks = wo.job_plan.tasks.all() if wo.job_plan else []
    return render_template('work_orders/detail.html', wo=wo, attachments=attachments,
                           previous_wo=previous, next_wo=following,
                           position=position, total=total,
                           list_args=request.args.to_dict(flat=False),
                           related=related_attachments(wo), tasks=tasks,
                           materials=wo.materials, tools=wo.tools, today=date.today())


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    wo = db.get_or_404(WorkOrder, id)
    refusal = _refuse_if_archived(wo)
    if refusal:
        return refusal
    options = _form_options(wo)

    if request.method == 'POST':
        validate_csrf()
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required.', 'error')
            return render_template('work_orders/form.html', wo=wo, **options)

        was_completed = wo.status == 'completed'
        had_completed_date = wo.completed_date is not None
        wo.title = title
        wo.wo_type = choice(request.form.get('wo_type'), WO_TYPES, wo.wo_type)
        wo.status = choice(request.form.get('status'), WO_STATUSES, wo.status)
        wo.priority = choice(request.form.get('priority'), WO_PRIORITIES, wo.priority)
        wo.asset_id = parse_int(request.form.get('asset_id'))
        wo.location_id = parse_int(request.form.get('location_id'))
        wo.job_plan_id = parse_int(request.form.get('job_plan_id'))
        wo.assigned_to = parse_int(request.form.get('assigned_to'))
        wo.due_date = parse_date(request.form.get('due_date'))
        wo.overdue_grace_days = parse_int(request.form.get('overdue_grace_days'), minimum=0) or 0
        wo.description = request.form.get('description', '').strip() or None
        wo.notes = request.form.get('notes', '').strip() or None

        # Manual date wins; blank on a completed work order falls back to today.
        # Reopening keeps the date, so the record of when it was finished stands.
        wo.completed_date = _resolve_completed_date(wo.status, wo.completed_date)

        # Entering a completion date says the job is done, so the status follows
        # rather than leaving a work order that records when it finished while
        # insisting it is still open.
        #
        # Only on the *transition* — a date appearing where there was none.
        # Reacting to the date merely being present would make a completed work
        # order impossible to cancel: `_resolve_completed_date` deliberately
        # keeps the date across that change, so every save would snap the status
        # back to completed.
        if wo.completed_date and not had_completed_date and wo.status != 'completed':
            wo.status = 'completed'
            flash('Marked completed, because a completion date was entered.', 'info')

        _store_form_uploads(wo.id, commit=False)
        _save_items(wo)
        # Attaching a job plan to a work order that has no lines of its own
        # brings the plan's list across.
        copy_job_plan_items(wo)
        # Materials roll onto the asset the moment the job is marked complete,
        # and only then — re-saving a completed work order must not count twice.
        if wo.status == 'completed' and not was_completed:
            record_materials_on_asset(wo)
        # A floating PM re-anchors to this completion date.
        sync_pm_schedule(wo)
        db.session.commit()
        flash('Work order updated.', 'success')
        return redirect(url_for('work_orders.detail', id=id))

    return render_template('work_orders/form.html', wo=wo, **options)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    wo = db.get_or_404(WorkOrder, id)
    # Whether an archive may be emptied is a house rule, not a law, so it is a
    # setting. On by default: an archive with no bin beside it is a problem of
    # its own. Checked here rather than trusted to the hidden button.
    if wo.is_archived and not archived_deletion_allowed():
        flash('Deleting archived work orders is switched off in Settings.',
              'error')
        return redirect(url_for('work_orders.detail', id=id))
    purge_entity_attachments(ENTITY, id)
    db.session.delete(wo)
    db.session.commit()
    flash('Work order deleted.', 'success')
    return redirect(url_for('work_orders.index'))


@bp.route('/<int:id>/archive', methods=['POST'])
@login_required
def archive(id):
    """Finalise a completed work order. One-way, so it is its own action rather
    than a value on the status dropdown."""
    validate_csrf()
    wo = db.get_or_404(WorkOrder, id)
    try:
        archive_work_order(wo)
    except NotArchivable as error:
        flash(str(error), 'error')
        return redirect(url_for('work_orders.detail', id=id))

    flash(f'{wo.wo_number} archived. Its details are now frozen and it can no '
          'longer be edited.', 'success')
    return redirect(url_for('work_orders.detail', id=id))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    wo = db.get_or_404(WorkOrder, id)
    refusal = _refuse_if_archived(wo)
    if refusal:
        return refusal
    rows = named_uploads(request.files.getlist('file'),
                         request.form.get('display_name', '').strip() or None)
    if not rows:
        flash('No file selected.', 'error')
        return redirect(url_for('work_orders.detail', id=id))

    saved, errors = store_uploads(ENTITY, id, rows, current_user.id)
    for message in errors:
        flash(message, 'error')
    if saved:
        db.session.commit()
        count = len(saved)
        flash(f"{count} file{'' if count == 1 else 's'} uploaded.", 'success')
    return redirect(url_for('work_orders.detail', id=id))
