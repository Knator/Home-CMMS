from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.job_plans import bp
from app.extensions import db
from app.models.job_plan import JobPlan, JobPlanTask
from app.models.attachment import Attachment
from app.utils import validate_csrf, allowed_file, save_attachment


@bp.route('/')
@login_required
def list():
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
    job_plan = JobPlan.query.get_or_404(id)
    tasks = job_plan.tasks.all()
    attachments = Attachment.query.filter_by(entity_type='job_plan', entity_id=id).order_by(Attachment.uploaded_at.desc()).all()
    return render_template('job_plans/detail.html', job_plan=job_plan, tasks=tasks, attachments=attachments)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    job_plan = JobPlan.query.get_or_404(id)
    if request.method == 'POST':
        validate_csrf()
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'error')
            return render_template('job_plans/form.html', job_plan=job_plan)

        job_plan.name = name
        job_plan.description = request.form.get('description', '').strip() or None
        job_plan.notes = request.form.get('notes', '').strip() or None

        # Replace all tasks
        JobPlanTask.query.filter_by(job_plan_id=id).delete()
        _save_tasks(job_plan)
        db.session.commit()
        flash('Job plan updated.', 'success')
        return redirect(url_for('job_plans.detail', id=id))

    return render_template('job_plans/form.html', job_plan=job_plan)


def _save_tasks(job_plan):
    task_count = int(request.form.get('task_count', 0))
    for i in range(task_count):
        desc = request.form.get(f'task_{i}_description', '').strip()
        if not desc:
            continue
        mins_raw = request.form.get(f'task_{i}_minutes', '').strip()
        mins = int(mins_raw) if mins_raw.isdigit() else None
        task = JobPlanTask(
            job_plan_id=job_plan.id,
            sequence=i + 1,
            description=desc,
            estimated_minutes=mins,
        )
        db.session.add(task)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    job_plan = JobPlan.query.get_or_404(id)
    db.session.delete(job_plan)
    db.session.commit()
    flash('Job plan deleted.', 'success')
    return redirect(url_for('job_plans.list'))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    JobPlan.query.get_or_404(id)
    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('job_plans.detail', id=id))
    if not allowed_file(file.filename):
        flash('File type not allowed.', 'error')
        return redirect(url_for('job_plans.detail', id=id))

    stored, original, size, mime = save_attachment(file, 'job_plan', id)
    att = Attachment(
        entity_type='job_plan', entity_id=id,
        stored_filename=stored, original_filename=original,
        file_size=size, mime_type=mime, uploaded_by=current_user.id,
    )
    db.session.add(att)
    db.session.commit()
    flash('File uploaded.', 'success')
    return redirect(url_for('job_plans.detail', id=id))
