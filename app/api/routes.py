"""Work order API.

Records are referenced by their human-facing numbers (AST-00001, LOC-00003)
rather than names: names are not unique for assets, and only unique per parent
for locations, so a name is not something a client can reliably address.
"""
from datetime import date

from flask import g, jsonify, request, url_for

from app.api import bp
from app.api.auth import api_token_required
from app.api.errors import bad_request, not_found
from app.extensions import db
from app.models.asset import Asset
from app.models.job_plan import JobPlan
from app.models.location import Location
from app.models.mixins import STATUS_ACTIVE
from app.models.user import User
from app.models.work_order import WorkOrder, WO_PRIORITIES, WO_STATUSES, WO_TYPES
from app.services import create_work_order, related_attachments
from app.utils import parse_date

MAX_TITLE = 200


def work_order_json(wo):
    return {
        'wo_number': wo.wo_number,
        'title': wo.title,
        'status': wo.status,
        'priority': wo.priority,
        'type': wo.wo_type,
        'asset_number': wo.asset.asset_number if wo.asset else None,
        'location_number': wo.location.location_number if wo.location else None,
        'job_plan': wo.job_plan.name if wo.job_plan else None,
        'assigned_to': wo.assignee.username if wo.assignee else None,
        'due_date': wo.due_date.isoformat() if wo.due_date else None,
        'completed_date': wo.completed_date.isoformat() if wo.completed_date else None,
        'overdue_grace_days': wo.overdue_grace_days,
        'is_overdue': wo.is_overdue,
        'description': wo.description,
        'notes': wo.notes,
        'created_at': wo.created_at.isoformat() if wo.created_at else None,
        'url': url_for('work_orders.detail', id=wo.id, _external=True),
    }


def _lookup(model, number_field, number, label, errors, field):
    """Resolve a record by its number, recording a precise error if it fails."""
    if number is None:
        return None
    if not isinstance(number, str) or not number.strip():
        errors[field] = f'{label} number must be a non-empty string.'
        return None

    record = model.query.filter(number_field == number.strip()).first()
    if record is None:
        errors[field] = f"No {label.lower()} exists with number '{number.strip()}'."
        return None
    if record.status != STATUS_ACTIVE:
        errors[field] = (f"{label} '{number.strip()}' is {record.status} and cannot be "
                         'used for new work.')
        return None
    return record


def _validate_payload(data):
    """Turn a request body into work order fields, or a map of field errors."""
    errors = {}

    title = data.get('title')
    if not isinstance(title, str) or not title.strip():
        errors['title'] = 'A non-empty title is required.'
    elif len(title.strip()) > MAX_TITLE:
        errors['title'] = f'Title must be {MAX_TITLE} characters or fewer.'

    asset = _lookup(Asset, Asset.asset_number, data.get('asset_number'),
                    'Asset', errors, 'asset_number')
    location = _lookup(Location, Location.location_number, data.get('location_number'),
                       'Location', errors, 'location_number')

    for field, allowed, default in (('priority', WO_PRIORITIES, 'medium'),
                                    ('status', WO_STATUSES, 'open'),
                                    ('type', WO_TYPES, 'unplanned')):
        value = data.get(field)
        if value is not None and value not in allowed:
            errors[field] = f"Must be one of: {', '.join(allowed)}."

    due_date = None
    if data.get('due_date') is not None:
        due_date = parse_date(data.get('due_date'))
        if due_date is None:
            errors['due_date'] = 'Must be a date in YYYY-MM-DD format.'

    completed_date = None
    if data.get('completed_date') is not None:
        completed_date = parse_date(data.get('completed_date'))
        if completed_date is None:
            errors['completed_date'] = 'Must be a date in YYYY-MM-DD format.'

    job_plan = None
    if data.get('job_plan') is not None:
        job_plan = JobPlan.query.filter_by(name=str(data['job_plan']).strip()).first()
        if job_plan is None:
            errors['job_plan'] = f"No job plan named '{data['job_plan']}'."

    assignee = None
    if data.get('assigned_to') is not None:
        assignee = User.query.filter_by(username=str(data['assigned_to']).strip()).first()
        if assignee is None or not assignee.is_active:
            errors['assigned_to'] = f"No active user named '{data['assigned_to']}'."

    grace = data.get('overdue_grace_days', 0)
    if not isinstance(grace, int) or isinstance(grace, bool) or grace < 0:
        errors['overdue_grace_days'] = 'Must be a non-negative whole number.'
        grace = 0

    if errors:
        return None, errors

    status = data.get('status', 'open')
    # An asset knows where it lives, so a caller need not repeat it.
    if location is None and asset is not None:
        location = asset.location

    return {
        'title': title.strip(),
        'wo_type': data.get('type', 'unplanned'),
        'status': status,
        'priority': data.get('priority', 'medium'),
        'asset_id': asset.id if asset else None,
        'location_id': location.id if location else None,
        'job_plan_id': job_plan.id if job_plan else None,
        'assigned_to': assignee.id if assignee else None,
        'due_date': due_date,
        'completed_date': completed_date or (date.today() if status == 'completed' else None),
        'overdue_grace_days': grace,
        'description': (data.get('description') or '').strip() or None,
        'notes': (data.get('notes') or '').strip() or None,
        'created_by': g.api_user.id,
    }, None


@bp.route('/work-orders', methods=['POST'])
@api_token_required
def create_work_order_api():
    if not request.is_json:
        return bad_request('Request body must be JSON with Content-Type: application/json.')

    try:
        data = request.get_json()
    except Exception:
        return bad_request('Request body is not valid JSON.')

    if not isinstance(data, dict):
        return bad_request('Request body must be a JSON object.')

    fields, errors = _validate_payload(data)
    if errors:
        return bad_request('The work order could not be created.', errors)

    wo = create_work_order(**fields)
    response = jsonify(work_order_json(wo))
    response.status_code = 201
    response.headers['Location'] = url_for('api.get_work_order', wo_number=wo.wo_number,
                                           _external=True)
    return response


@bp.route('/work-orders/<wo_number>')
@api_token_required
def get_work_order(wo_number):
    wo = WorkOrder.query.filter_by(wo_number=wo_number).first()
    if wo is None:
        return not_found(f"No work order with number '{wo_number}'.")
    payload = work_order_json(wo)
    payload['related_documents'] = [
        {'filename': item['attachment'].label,
         'source': f"{item['source_label']}: {item['source_name']}"}
        for item in related_attachments(wo)
    ]
    return jsonify(payload)


@bp.route('/work-orders')
@api_token_required
def list_work_orders():
    """Recent work orders, newest first. Filterable by status."""
    query = WorkOrder.query
    status = request.args.get('status')
    if status:
        if status not in WO_STATUSES:
            return bad_request('Unknown status.',
                               {'status': f"Must be one of: {', '.join(WO_STATUSES)}."})
        query = query.filter_by(status=status)

    limit = request.args.get('limit', type=int) or 50
    limit = max(1, min(limit, 200))
    rows = query.order_by(WorkOrder.created_at.desc()).limit(limit).all()
    return jsonify({'count': len(rows), 'work_orders': [work_order_json(w) for w in rows]})


@bp.route('/assets')
@api_token_required
def list_assets():
    """So a client can discover the numbers it needs to reference."""
    rows = Asset.query.order_by(Asset.asset_number).all()
    return jsonify({'count': len(rows), 'assets': [
        {'asset_number': a.asset_number, 'name': a.name, 'status': a.status,
         'location_number': a.location.location_number if a.location else None}
        for a in rows
    ]})


@bp.route('/locations')
@api_token_required
def list_locations():
    rows = Location.query.order_by(Location.location_number).all()
    return jsonify({'count': len(rows), 'locations': [
        {'location_number': l.location_number, 'name': l.name, 'status': l.status,
         'path': l.path_label}
        for l in rows
    ]})
