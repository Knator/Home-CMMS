from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.job_plans import bp
from app.extensions import db
from app.models.job_plan import (
    JobPlan, JobPlanTask, JobPlanItem, ITEM_MATERIAL, ITEM_TOOL,
)
from app.models.attachment import Attachment
from app.utils import (
    validate_csrf, purge_entity_attachments, store_uploads, named_uploads, upload_rows_from_form,
    parse_int,
)

ENTITY = 'job_plan'
MAX_TASKS = 200
MAX_ITEMS = 200


@bp.route('/')
@login_required
def index():
    job_plans = JobPlan.query.order_by(JobPlan.name).all()
    return render_template('job_plans/list.html', job_plans=job_plans)


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
        flash('Job plan created.', 'success')
        return redirect(url_for('job_plans.detail', id=job_plan.id))

    return render_template('job_plans/form.html', job_plan=None)


@bp.route('/<int:id>')
@login_required
def detail(id):
    job_plan = db.get_or_404(JobPlan, id)
    tasks = job_plan.tasks.all()
    attachments = (
        Attachment.query
        .filter_by(entity_type=ENTITY, entity_id=id)
        .order_by(Attachment.uploaded_at.desc())
        .all()
    )
    return render_template('job_plans/detail.html', job_plan=job_plan, tasks=tasks,
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
        return redirect(url_for('job_plans.detail', id=id))

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
    return redirect(url_for('job_plans.index'))


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
