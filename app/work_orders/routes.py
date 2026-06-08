from datetime import date
from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.work_orders import bp
from app.extensions import db
from app.models.work_order import WorkOrder, WO_STATUSES, WO_PRIORITIES, WO_TYPES
from app.models.asset import Asset
from app.models.location import Location
from app.models.job_plan import JobPlan
from app.models.user import User
from app.models.attachment import Attachment
from app.utils import validate_csrf, allowed_file, save_attachment


def _parse_date(val):
    try:
        return date.fromisoformat(val) if val else None
    except ValueError:
        return None


@bp.route('/')
@login_required
def list():
    status = request.args.get('status', '')
    wo_type = request.args.get('type', '')
    priority = request.args.get('priority', '')

    q = WorkOrder.query
    if status:
        q = q.filter_by(status=status)
    if wo_type:
        q = q.filter_by(wo_type=wo_type)
    if priority:
        q = q.filter_by(priority=priority)

    work_orders = q.order_by(WorkOrder.created_at.desc()).all()
    return render_template(
        'work_orders/list.html',
        work_orders=work_orders,
        statuses=WO_STATUSES, priorities=WO_PRIORITIES, wo_types=WO_TYPES,
        selected_status=status, selected_type=wo_type, selected_priority=priority,
        today=date.today(),
    )


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    assets = Asset.query.order_by(Asset.name).all()
    locations = Location.query.order_by(Location.name).all()
    job_plans = JobPlan.query.order_by(JobPlan.name).all()
    users = User.query.filter_by(is_active=True).order_by(User.username).all()

    if request.method == 'POST':
        validate_csrf()
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required.', 'error')
            return render_template('work_orders/form.html', wo=None,
                                   assets=assets, locations=locations,
                                   job_plans=job_plans, users=users,
                                   statuses=WO_STATUSES, priorities=WO_PRIORITIES, wo_types=WO_TYPES)

        wo = WorkOrder(
            wo_number=WorkOrder.generate_wo_number(),
            title=title,
            wo_type=request.form.get('wo_type', 'unplanned'),
            status=request.form.get('status', 'open'),
            priority=request.form.get('priority', 'medium'),
            asset_id=request.form.get('asset_id') or None,
            location_id=request.form.get('location_id') or None,
            job_plan_id=request.form.get('job_plan_id') or None,
            assigned_to=request.form.get('assigned_to') or None,
            due_date=_parse_date(request.form.get('due_date')),
            description=request.form.get('description', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
            created_by=current_user.id,
        )
        db.session.add(wo)
        db.session.commit()
        flash(f'Work order {wo.wo_number} created.', 'success')
        return redirect(url_for('work_orders.detail', id=wo.id))

    return render_template('work_orders/form.html', wo=None,
                           assets=assets, locations=locations,
                           job_plans=job_plans, users=users,
                           statuses=WO_STATUSES, priorities=WO_PRIORITIES, wo_types=WO_TYPES)


@bp.route('/<int:id>')
@login_required
def detail(id):
    wo = WorkOrder.query.get_or_404(id)
    attachments = Attachment.query.filter_by(entity_type='work_order', entity_id=id).order_by(Attachment.uploaded_at.desc()).all()
    tasks = wo.job_plan.tasks.all() if wo.job_plan else []
    return render_template('work_orders/detail.html', wo=wo, attachments=attachments, tasks=tasks, today=date.today())


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    wo = WorkOrder.query.get_or_404(id)
    assets = Asset.query.order_by(Asset.name).all()
    locations = Location.query.order_by(Location.name).all()
    job_plans = JobPlan.query.order_by(JobPlan.name).all()
    users = User.query.filter_by(is_active=True).order_by(User.username).all()

    if request.method == 'POST':
        validate_csrf()
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required.', 'error')
            return render_template('work_orders/form.html', wo=wo,
                                   assets=assets, locations=locations,
                                   job_plans=job_plans, users=users,
                                   statuses=WO_STATUSES, priorities=WO_PRIORITIES, wo_types=WO_TYPES)

        wo.title = title
        wo.wo_type = request.form.get('wo_type', wo.wo_type)
        wo.status = request.form.get('status', wo.status)
        wo.priority = request.form.get('priority', wo.priority)
        wo.asset_id = request.form.get('asset_id') or None
        wo.location_id = request.form.get('location_id') or None
        wo.job_plan_id = request.form.get('job_plan_id') or None
        wo.assigned_to = request.form.get('assigned_to') or None
        wo.due_date = _parse_date(request.form.get('due_date'))
        wo.description = request.form.get('description', '').strip() or None
        wo.notes = request.form.get('notes', '').strip() or None

        from datetime import datetime
        if wo.status == 'completed' and not wo.completed_date:
            wo.completed_date = datetime.utcnow()
        elif wo.status != 'completed':
            wo.completed_date = None

        db.session.commit()
        flash('Work order updated.', 'success')
        return redirect(url_for('work_orders.detail', id=id))

    return render_template('work_orders/form.html', wo=wo,
                           assets=assets, locations=locations,
                           job_plans=job_plans, users=users,
                           statuses=WO_STATUSES, priorities=WO_PRIORITIES, wo_types=WO_TYPES)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    wo = WorkOrder.query.get_or_404(id)
    db.session.delete(wo)
    db.session.commit()
    flash('Work order deleted.', 'success')
    return redirect(url_for('work_orders.list'))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    WorkOrder.query.get_or_404(id)
    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('work_orders.detail', id=id))
    if not allowed_file(file.filename):
        flash('File type not allowed.', 'error')
        return redirect(url_for('work_orders.detail', id=id))

    stored, original, size, mime = save_attachment(file, 'work_order', id)
    att = Attachment(
        entity_type='work_order', entity_id=id,
        stored_filename=stored, original_filename=original,
        file_size=size, mime_type=mime, uploaded_by=current_user.id,
    )
    db.session.add(att)
    db.session.commit()
    flash('File uploaded.', 'success')
    return redirect(url_for('work_orders.detail', id=id))
