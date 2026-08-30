from datetime import date

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.pms import bp
from app.extensions import db
from app.models.pm import PM
from app.models.asset import Asset
from app.models.location import Location
from app.models.job_plan import JobPlan
from app.models.work_order import WorkOrder
from app.models.attachment import Attachment
from app.services import generate_work_order_for_pm
from app.utils import (
    validate_csrf, allowed_file, save_attachment, purge_entity_attachments,
    parse_date, parse_int,
)

ENTITY = 'pm'


def _form_options():
    return dict(
        assets=Asset.query.order_by(Asset.name).all(),
        locations=Location.query.order_by(Location.name).all(),
        job_plans=JobPlan.query.order_by(JobPlan.name).all(),
    )


def _read_form():
    """Pull and validate the PM fields shared by create and edit."""
    name = request.form.get('name', '').strip()
    interval = parse_int(request.form.get('interval_days', '').strip(), minimum=1)
    next_due = parse_date(request.form.get('next_due_date', '').strip())

    errors = []
    if not name:
        errors.append('Name is required.')
    if interval is None:
        errors.append('Interval must be a positive number of days.')
    if not next_due:
        errors.append('Next due date is required.')
    return name, interval, next_due, errors


@bp.route('/')
@login_required
def index():
    active_only = request.args.get('show', 'active') != 'all'
    q = PM.query
    if active_only:
        q = q.filter_by(is_active=True)
    pms = q.order_by(PM.next_due_date).all()
    return render_template('pms/list.html', pms=pms, today=date.today(), active_only=active_only)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    options = _form_options()

    if request.method == 'POST':
        validate_csrf()
        name, interval, next_due, errors = _read_form()
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('pms/form.html', pm=None, **options)

        pm = PM(
            name=name,
            asset_id=parse_int(request.form.get('asset_id')),
            location_id=parse_int(request.form.get('location_id')),
            job_plan_id=parse_int(request.form.get('job_plan_id')),
            interval_days=interval,
            next_due_date=next_due,
            is_active=True,
            notes=request.form.get('notes', '').strip() or None,
            created_by=current_user.id,
        )
        db.session.add(pm)
        db.session.commit()
        flash('PM schedule created.', 'success')
        return redirect(url_for('pms.detail', id=pm.id))

    return render_template('pms/form.html', pm=None, **options)


@bp.route('/<int:id>')
@login_required
def detail(id):
    pm = db.get_or_404(PM, id)
    generated_wos = pm.generated_work_orders.order_by(WorkOrder.created_at.desc()).limit(20).all()
    attachments = (
        Attachment.query
        .filter_by(entity_type=ENTITY, entity_id=id)
        .order_by(Attachment.uploaded_at.desc())
        .all()
    )
    return render_template('pms/detail.html', pm=pm, generated_wos=generated_wos,
                           attachments=attachments, today=date.today())


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    pm = db.get_or_404(PM, id)
    options = _form_options()

    if request.method == 'POST':
        validate_csrf()
        name, interval, next_due, errors = _read_form()
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('pms/form.html', pm=pm, **options)

        pm.name = name
        pm.asset_id = parse_int(request.form.get('asset_id'))
        pm.location_id = parse_int(request.form.get('location_id'))
        pm.job_plan_id = parse_int(request.form.get('job_plan_id'))
        pm.interval_days = interval
        pm.next_due_date = next_due
        pm.is_active = bool(request.form.get('is_active'))
        pm.notes = request.form.get('notes', '').strip() or None
        db.session.commit()
        flash('PM schedule updated.', 'success')
        return redirect(url_for('pms.detail', id=id))

    return render_template('pms/form.html', pm=pm, **options)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    pm = db.get_or_404(PM, id)
    purge_entity_attachments(ENTITY, id)
    db.session.delete(pm)
    db.session.commit()
    flash('PM schedule deleted.', 'success')
    return redirect(url_for('pms.index'))


@bp.route('/<int:id>/generate', methods=['POST'])
@login_required
def generate_now(id):
    validate_csrf()
    pm = db.get_or_404(PM, id)
    if not pm.is_active:
        flash('This PM schedule is inactive. Activate it before generating a work order.', 'error')
        return redirect(url_for('pms.detail', id=id))

    wo = generate_work_order_for_pm(
        pm,
        created_by=current_user.id,
        description=f"Manually generated from PM schedule: {pm.name}",
    )
    flash(f'Work order {wo.wo_number} generated.', 'success')
    return redirect(url_for('pms.detail', id=id))


@bp.route('/<int:id>/toggle', methods=['POST'])
@login_required
def toggle_active(id):
    validate_csrf()
    pm = db.get_or_404(PM, id)
    pm.is_active = not pm.is_active
    db.session.commit()
    state = 'activated' if pm.is_active else 'deactivated'
    flash(f'PM schedule {state}.', 'success')
    return redirect(url_for('pms.detail', id=id))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    db.get_or_404(PM, id)
    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('pms.detail', id=id))
    if not allowed_file(file.filename):
        flash('File type not allowed.', 'error')
        return redirect(url_for('pms.detail', id=id))

    stored, original, size, mime = save_attachment(file, ENTITY, id)
    att = Attachment(
        entity_type=ENTITY, entity_id=id,
        stored_filename=stored, original_filename=original,
        file_size=size, mime_type=mime, uploaded_by=current_user.id,
    )
    db.session.add(att)
    db.session.commit()
    flash('File uploaded.', 'success')
    return redirect(url_for('pms.detail', id=id))
