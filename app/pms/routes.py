from datetime import date, timedelta
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
from app.utils import validate_csrf, allowed_file, save_attachment


def _parse_date(val):
    try:
        return date.fromisoformat(val) if val else None
    except ValueError:
        return None


@bp.route('/')
@login_required
def list():
    active_only = request.args.get('show', 'active') != 'all'
    q = PM.query
    if active_only:
        q = q.filter_by(is_active=True)
    pms = q.order_by(PM.next_due_date).all()
    today = date.today()
    return render_template('pms/list.html', pms=pms, today=today, active_only=active_only)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    assets = Asset.query.order_by(Asset.name).all()
    locations = Location.query.order_by(Location.name).all()
    job_plans = JobPlan.query.order_by(JobPlan.name).all()

    if request.method == 'POST':
        validate_csrf()
        name = request.form.get('name', '').strip()
        interval_raw = request.form.get('interval_days', '').strip()
        next_due_raw = request.form.get('next_due_date', '').strip()

        errors = []
        if not name:
            errors.append('Name is required.')
        if not interval_raw.isdigit() or int(interval_raw) < 1:
            errors.append('Interval must be a positive number of days.')
        next_due = _parse_date(next_due_raw)
        if not next_due:
            errors.append('Next due date is required.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('pms/form.html', pm=None, assets=assets, locations=locations, job_plans=job_plans)

        pm = PM(
            name=name,
            asset_id=request.form.get('asset_id') or None,
            location_id=request.form.get('location_id') or None,
            job_plan_id=request.form.get('job_plan_id') or None,
            interval_days=int(interval_raw),
            next_due_date=next_due,
            is_active=True,
            notes=request.form.get('notes', '').strip() or None,
            created_by=current_user.id,
        )
        db.session.add(pm)
        db.session.commit()
        flash('PM schedule created.', 'success')
        return redirect(url_for('pms.detail', id=pm.id))

    return render_template('pms/form.html', pm=None, assets=assets, locations=locations, job_plans=job_plans)


@bp.route('/<int:id>')
@login_required
def detail(id):
    pm = PM.query.get_or_404(id)
    generated_wos = pm.generated_work_orders.order_by(WorkOrder.created_at.desc()).limit(20).all()
    attachments = Attachment.query.filter_by(entity_type='pm', entity_id=id).order_by(Attachment.uploaded_at.desc()).all()
    today = date.today()
    return render_template('pms/detail.html', pm=pm, generated_wos=generated_wos, attachments=attachments, today=today)


@bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    pm = PM.query.get_or_404(id)
    assets = Asset.query.order_by(Asset.name).all()
    locations = Location.query.order_by(Location.name).all()
    job_plans = JobPlan.query.order_by(JobPlan.name).all()

    if request.method == 'POST':
        validate_csrf()
        name = request.form.get('name', '').strip()
        interval_raw = request.form.get('interval_days', '').strip()
        next_due_raw = request.form.get('next_due_date', '').strip()

        errors = []
        if not name:
            errors.append('Name is required.')
        if not interval_raw.isdigit() or int(interval_raw) < 1:
            errors.append('Interval must be a positive number of days.')
        next_due = _parse_date(next_due_raw)
        if not next_due:
            errors.append('Next due date is required.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('pms/form.html', pm=pm, assets=assets, locations=locations, job_plans=job_plans)

        pm.name = name
        pm.asset_id = request.form.get('asset_id') or None
        pm.location_id = request.form.get('location_id') or None
        pm.job_plan_id = request.form.get('job_plan_id') or None
        pm.interval_days = int(interval_raw)
        pm.next_due_date = next_due
        pm.is_active = bool(request.form.get('is_active'))
        pm.notes = request.form.get('notes', '').strip() or None
        db.session.commit()
        flash('PM schedule updated.', 'success')
        return redirect(url_for('pms.detail', id=id))

    return render_template('pms/form.html', pm=pm, assets=assets, locations=locations, job_plans=job_plans)


@bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete(id):
    validate_csrf()
    pm = PM.query.get_or_404(id)
    db.session.delete(pm)
    db.session.commit()
    flash('PM schedule deleted.', 'success')
    return redirect(url_for('pms.list'))


@bp.route('/<int:id>/generate', methods=['POST'])
@login_required
def generate_now(id):
    validate_csrf()
    pm = PM.query.get_or_404(id)
    wo = WorkOrder(
        wo_number=WorkOrder.generate_wo_number(),
        title=pm.name,
        wo_type='planned',
        status='open',
        priority='medium',
        asset_id=pm.asset_id,
        location_id=pm.location_id,
        job_plan_id=pm.job_plan_id,
        pm_id=pm.id,
        due_date=pm.next_due_date,
        description=f"Manually generated from PM schedule: {pm.name}",
        created_by=current_user.id,
    )
    db.session.add(wo)
    pm.advance_schedule()
    db.session.commit()
    flash(f'Work order {wo.wo_number} generated.', 'success')
    return redirect(url_for('pms.detail', id=id))


@bp.route('/<int:id>/toggle', methods=['POST'])
@login_required
def toggle_active(id):
    validate_csrf()
    pm = PM.query.get_or_404(id)
    pm.is_active = not pm.is_active
    db.session.commit()
    state = 'activated' if pm.is_active else 'deactivated'
    flash(f'PM schedule {state}.', 'success')
    return redirect(url_for('pms.detail', id=id))


@bp.route('/<int:id>/attachments', methods=['POST'])
@login_required
def upload_attachment(id):
    validate_csrf()
    PM.query.get_or_404(id)
    file = request.files.get('file')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('pms.detail', id=id))
    if not allowed_file(file.filename):
        flash('File type not allowed.', 'error')
        return redirect(url_for('pms.detail', id=id))

    stored, original, size, mime = save_attachment(file, 'pm', id)
    att = Attachment(
        entity_type='pm', entity_id=id,
        stored_filename=stored, original_filename=original,
        file_size=size, mime_type=mime, uploaded_by=current_user.id,
    )
    db.session.add(att)
    db.session.commit()
    flash('File uploaded.', 'success')
    return redirect(url_for('pms.detail', id=id))
