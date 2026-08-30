from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.job_plans import bp
from app.extensions import db
from app.models.job_plan import JobPlan, JobPlanTask
from app.models.attachment import Attachment
from app.utils import (
    validate_csrf, purge_entity_attachments, store_uploads, parse_int,
)

ENTITY = 'job_plan'
MAX_TASKS = 200


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
    return render_template('job_plans/detail.html', job_plan=job_plan, tasks=tasks, attachments=attachments)


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

        # Replace all tasks. Deleted through the session rather than a bulk
        # query so the delete-orphan cascade and the identity map stay in sync.
        for task in job_plan.tasks.all():
            db.session.delete(task)
        db.session.flush()

        _save_tasks(job_plan)
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
    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('job_plans.detail', id=id))

    display_name = request.form.get('display_name', '').strip() or None
    saved, errors = store_uploads(ENTITY, id, [(file, display_name)], current_user.id)
    for message in errors:
        flash(message, 'error')
    if saved:
        db.session.commit()
        flash('File uploaded.', 'success')
    return redirect(url_for('job_plans.detail', id=id))
