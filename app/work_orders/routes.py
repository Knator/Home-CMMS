from datetime import date

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.work_orders import bp
from app.extensions import db
from app.models.work_order import WorkOrder, WO_STATUSES, WO_PRIORITIES, WO_TYPES
from app.models.job_plan import JobPlan
from app.models.user import User
from app.models.attachment import Attachment
from app.services import create_work_order, related_attachments, selectable_assets, selectable_locations
from app.utils import (
    validate_csrf, allowed_file, save_attachment, purge_entity_attachments,
    parse_date, parse_int, utcnow, choice,
)

ENTITY = 'work_order'


def _form_options(wo=None):
    """Pickers offer Active assets and locations only.

    A record that already points at something since retired keeps it listed, so
    editing an old work order cannot silently blank the field on save.
    """
    return dict(
        assets=selectable_assets(include_id=wo.asset_id if wo else None),
        locations=selectable_locations(include_id=wo.location_id if wo else None),
        job_plans=JobPlan.query.order_by(JobPlan.name).all(),
        users=User.query.filter_by(is_active=True).order_by(User.username).all(),
        statuses=WO_STATUSES, priorities=WO_PRIORITIES, wo_types=WO_TYPES,
    )


@bp.route('/')
@login_required
def index():
    status = request.args.get('status', '')
    wo_type = request.args.get('type', '')
    priority = request.args.get('priority', '')

    q = WorkOrder.query
    if status in WO_STATUSES:
        q = q.filter_by(status=status)
    else:
        status = ''
    if wo_type in WO_TYPES:
        q = q.filter_by(wo_type=wo_type)
    else:
        wo_type = ''
    if priority in WO_PRIORITIES:
        q = q.filter_by(priority=priority)
    else:
        priority = ''

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
    options = _form_options()

    if request.method == 'POST':
        validate_csrf()
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required.', 'error')
            return render_template('work_orders/form.html', wo=None, **options)

        status = choice(request.form.get('status'), WO_STATUSES, 'open')
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
            completed_date=utcnow() if status == 'completed' else None,
            description=request.form.get('description', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
            created_by=current_user.id,
        )
        flash(f'Work order {wo.wo_number} created.', 'success')
        return redirect(url_for('work_orders.detail', id=wo.id))

    return render_template('work_orders/form.html', wo=None, **options)


@bp.route('/<int:id>')
@login_required
def detail(id):
    wo = db.get_or_404(WorkOrder, id)
    attachments = (
        Attachment.query
        .filter_by(entity_type=ENTITY, entity_id=id)
        .order_by(Attachment.uploaded_at.desc())
        .all()
    )
    tasks = wo.job_plan.tasks.all() if wo.job_plan else []
    return render_template('work_orders/detail.html', wo=wo, attachments=attachments,
                           related=related_attachments(wo), tasks=tasks, today=date.today())


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    wo = db.get_or_404(WorkOrder, id)
    options = _form_options(wo)

    if request.method == 'POST':
        validate_csrf()
        title = request.form.get('title', '').strip()
        if not title:
            flash('Title is required.', 'error')
            return render_template('work_orders/form.html', wo=wo, **options)

        wo.title = title
        wo.wo_type = choice(request.form.get('wo_type'), WO_TYPES, wo.wo_type)
        wo.status = choice(request.form.get('status'), WO_STATUSES, wo.status)
        wo.priority = choice(request.form.get('priority'), WO_PRIORITIES, wo.priority)
        wo.asset_id = parse_int(request.form.get('asset_id'))
        wo.location_id = parse_int(request.form.get('location_id'))
        wo.job_plan_id = parse_int(request.form.get('job_plan_id'))
        wo.assigned_to = parse_int(request.form.get('assigned_to'))
        wo.due_date = parse_date(request.form.get('due_date'))
        wo.description = request.form.get('description', '').strip() or None
        wo.notes = request.form.get('notes', '').strip() or None

        # Stamp the first completion and keep it. Reopening a work order must
        # not erase the record of when it was finished.
        if wo.status == 'completed' and not wo.completed_date:
            wo.completed_date = utcnow()

        db.session.commit()
        flash('Work order updated.', 'success')
        return redirect(url_for('work_orders.detail', id=id))

    return render_template('work_orders/form.html', wo=wo, **options)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    wo = db.get_or_404(WorkOrder, id)
    purge_entity_attachments(ENTITY, id)
    db.session.delete(wo)
    db.session.commit()
    flash('Work order deleted.', 'success')
    return redirect(url_for('work_orders.index'))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    db.get_or_404(WorkOrder, id)
    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('work_orders.detail', id=id))
    if not allowed_file(file.filename):
        flash('File type not allowed.', 'error')
        return redirect(url_for('work_orders.detail', id=id))

    stored, original, size, mime = save_attachment(file, ENTITY, id)
    att = Attachment(
        entity_type=ENTITY, entity_id=id,
        stored_filename=stored, original_filename=original,
        file_size=size, mime_type=mime, uploaded_by=current_user.id,
    )
    db.session.add(att)
    db.session.commit()
    flash('File uploaded.', 'success')
    return redirect(url_for('work_orders.detail', id=id))
