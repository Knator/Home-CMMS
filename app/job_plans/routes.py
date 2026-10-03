from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.job_plans import bp
from app.extensions import db
from app.models.job_plan import (
    JobPlan, JobPlanTask, JobPlanItem, ITEM_MATERIAL, ITEM_TOOL,
)
from app.models.attachment import Attachment
from app.navigation import (carried_list_args, PAGE_SIZE, empty_page, neighbours,
                            page_number, paginate_list)
from app.search import (
    SearchTooSlow, compile_pattern, like_clause, regex_filter, too_slow_message,
)
from app.utils import (
    validate_csrf, purge_entity_attachments, store_uploads, named_uploads, upload_rows_from_form,
    is_embedded, embedded_created,
    parse_int,
)

ENTITY = 'job_plan'
MAX_TASKS = 200
MAX_ITEMS = 200


def _job_plan_query(args):
    """The filtered query, and the regex pattern if one applies."""
    search = args.get('q', '').strip()
    use_regex = bool(args.get('regex'))

    query = JobPlan.query
    pattern = None
    if search and use_regex:
        pattern, regex_error = compile_pattern(search)
        if regex_error:
            return None, None, ('invalid', regex_error)
    elif search:
        # The task descriptions live in another table, so they are reached with
        # an EXISTS rather than a join — a job plan with three matching tasks
        # should appear once, not three times.
        query = query.filter(db.or_(
            like_clause(search, JobPlan.name, JobPlan.description, JobPlan.notes),
            JobPlan.tasks.any(like_clause(search, JobPlanTask.description)),
        ))

    return query.order_by(JobPlan.name), pattern, None


def _filtered_page(args):
    query, pattern, problem = _job_plan_query(args)
    if problem:
        return empty_page(page_number(args)), problem

    page = page_number(args)
    if pattern is not None:
        # The regex reads task descriptions too, so the rows have to be loaded
        # either way; this is the one list where that was never avoidable.
        try:
            rows = regex_filter(pattern, query.all(), _searchable_text)
        except SearchTooSlow:
            return empty_page(page), ('slow', None)
        return paginate_list(rows, page), None
    return query.paginate(page=page, per_page=PAGE_SIZE, error_out=False), None


def _sequence_ids(args):
    query, pattern, problem = _job_plan_query(args)
    if problem:
        return []
    if pattern is not None:
        try:
            return [plan.id for plan in regex_filter(pattern, query.all(), _searchable_text)]
        except SearchTooSlow:
            return []
    return [row[0] for row in query.with_entities(JobPlan.id).all()]


@bp.route('/')
@login_required
def index():
    page, problem = _filtered_page(request.args)
    if problem:
        kind, detail_text = problem
        if kind == 'slow':
            # Not a syntax error, so it must not be reported as one.
            flash(too_slow_message().capitalize(), 'error')
        else:
            flash(f'That is not a valid regular expression: {detail_text}', 'error')


    # Only when there is nothing to show: one cheap existence check tells the
    # empty state whether the list is genuinely empty or merely filtered.
    any_records = bool(page.items) or JobPlan.query.first() is not None

    return render_template('job_plans/list.html', job_plans=page.items, page=page,
                           search=request.args.get('q', '').strip(),
                           use_regex=bool(request.args.get('regex')), any_records=any_records)


def _searchable_text(job_plan):
    """Everything a job plan search looks at, including its tasks."""
    yield job_plan.name
    yield job_plan.description
    yield job_plan.notes
    for task in job_plan.tasks:
        yield task.description


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        validate_csrf()
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'error')
            return render_template('job_plans/form.html', job_plan=None)

        job_plan = JobPlan(
            name=name,
            description=request.form.get('description', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
            created_by=current_user.id,
        )
        db.session.add(job_plan)
        db.session.flush()  # get job_plan.id before committing

        _save_tasks(job_plan)
        _save_items(job_plan)
        # Attachments are filed under the job plan's id, available after the flush.
        _store_form_uploads(job_plan.id)
        db.session.commit()
        if is_embedded():
            return embedded_created('job_plan', job_plan.id, job_plan.name)
        flash('Job plan created.', 'success')
        # The create form posted to its own URL, so the list's filters
        # came with it; Back from the new record returns to that list.
        return redirect(url_for('job_plans.detail', id=job_plan.id,
                                **carried_list_args(request.args)))

    return render_template('job_plans/form.html', job_plan=None)


@bp.route('/<int:id>')
@login_required
def detail(id):
    previous_id, next_id, position, total = neighbours(_sequence_ids(request.args), id)
    previous = db.session.get(JobPlan, previous_id) if previous_id else None
    following = db.session.get(JobPlan, next_id) if next_id else None
    job_plan = db.get_or_404(JobPlan, id)
    tasks = job_plan.tasks.all()
    attachments = (
        Attachment.query
        .filter_by(entity_type=ENTITY, entity_id=id)
        .order_by(Attachment.uploaded_at.desc())
        .all()
    )
    return render_template('job_plans/detail.html', job_plan=job_plan, tasks=tasks,
                           previous_plan=previous, next_plan=following,
                           position=position, total=total,
                           materials=job_plan.materials, tools=job_plan.tools,
                           attachments=attachments)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    job_plan = db.get_or_404(JobPlan, id)
    if request.method == 'POST':
        validate_csrf()
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'error')
            return render_template('job_plans/form.html', job_plan=job_plan)

        job_plan.name = name
        job_plan.description = request.form.get('description', '').strip() or None
        job_plan.notes = request.form.get('notes', '').strip() or None

        # Replace tasks, materials and tools. Deleted through the session rather
        # than a bulk query so the delete-orphan cascade and the identity map
        # stay in sync.
        for row in list(job_plan.tasks.all()) + list(job_plan.items.all()):
            db.session.delete(row)
        db.session.flush()

        _save_tasks(job_plan)
        _save_items(job_plan)
        _store_form_uploads(job_plan.id)
        db.session.commit()
        flash('Job plan updated.', 'success')
        # Back to the record with the list's filters still attached — the edit
        # form posted to its own URL, query string and all, so they are here.
        return redirect(url_for('job_plans.detail', id=id,
                                **carried_list_args(request.args)))

    return render_template('job_plans/form.html', job_plan=job_plan)


def _save_tasks(job_plan):
    """Read the dynamic task rows off the submitted form.

    task_count comes from a hidden field the browser maintains, so it is
    untrusted: parse it defensively and cap the loop.
    """
    task_count = parse_int(request.form.get('task_count'), minimum=0) or 0
    sequence = 1
    for i in range(min(task_count, MAX_TASKS)):
        desc = request.form.get(f'task_{i}_description', '').strip()
        if not desc:
            continue
        task = JobPlanTask(
            job_plan_id=job_plan.id,
            sequence=sequence,
            description=desc,
            estimated_minutes=parse_int(request.form.get(f'task_{i}_minutes'), minimum=1),
        )
        db.session.add(task)
        sequence += 1


def _save_items(job_plan):
    """Read the material and tool rows off the submitted form.

    Same shape as the task rows: a browser-maintained count, so untrusted and
    capped. Rows with no description are skipped and the sequence stays gapless.
    """
    for kind, prefix in ((ITEM_MATERIAL, 'material'), (ITEM_TOOL, 'tool')):
        count = parse_int(request.form.get(f'{prefix}_count'), minimum=0) or 0
        sequence = 1
        for i in range(min(count, MAX_ITEMS)):
            description = request.form.get(f'{prefix}_{i}_description', '').strip()
            if not description:
                continue
            db.session.add(JobPlanItem(
                job_plan_id=job_plan.id,
                kind=kind,
                sequence=sequence,
                description=description,
                quantity=request.form.get(f'{prefix}_{i}_quantity', '').strip() or None,
                part_number=request.form.get(f'{prefix}_{i}_part_number', '').strip() or None,
            ))
            sequence += 1


def _store_form_uploads(job_plan_id):
    """Persist any files attached on the create/edit form."""
    rows = upload_rows_from_form()
    if not rows:
        return
    saved, errors = store_uploads(ENTITY, job_plan_id, rows, current_user.id)
    for message in errors:
        flash(message, 'error')
    if saved:
        count = len(saved)
        flash(f"{count} file{'' if count == 1 else 's'} attached.", 'success')


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    job_plan = db.get_or_404(JobPlan, id)
    purge_entity_attachments(ENTITY, id)
    db.session.delete(job_plan)
    db.session.commit()
    flash('Job plan deleted.', 'success')
    return redirect(url_for('job_plans.index', **carried_list_args(request.args)))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    db.get_or_404(JobPlan, id)
    rows = named_uploads(request.files.getlist('file'),
                         request.form.get('display_name', '').strip() or None)
    if not rows:
        flash('No file selected.', 'error')
        return redirect(url_for('job_plans.detail', id=id))

    saved, errors = store_uploads(ENTITY, id, rows, current_user.id)
    for message in errors:
        flash(message, 'error')
    if saved:
        db.session.commit()
        count = len(saved)
        flash(f"{count} file{'' if count == 1 else 's'} uploaded.", 'success')
    return redirect(url_for('job_plans.detail', id=id))
