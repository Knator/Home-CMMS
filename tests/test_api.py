"""REST API for creating work orders.

Records are addressed by their numbers rather than names, because asset names
are not unique and location names are only unique per parent.
"""
import json
from datetime import date, timedelta

import pytest

from app.models.location import Location
from app.models.mixins import STATUS_DECOMMISSIONED, STATUS_INACTIVE
from app.models.api_token import ApiToken
from app.models.user import User
from app.models.work_order import WorkOrder
from app.services import create_asset, create_location, create_work_order
from tests.conftest import make_user


@pytest.fixture
def token(db, app):
    user = make_user('robot', role='user')
    _, raw = ApiToken.issue(user, 'Test integration')
    db.session.commit()
    return raw


@pytest.fixture
def auth(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def world(db, user):
    location = create_location(name='Basement')
    asset = create_asset(name='Furnace', location_id=location.id)
    return {'location': location, 'asset': asset}


def post(client, payload, headers):
    return client.post('/api/v1/work-orders', data=json.dumps(payload),
                       content_type='application/json', headers=headers)


# ── authentication ─────────────────────────────────────────────────────────

def test_creating_requires_a_token(client, db, world):
    response = post(client, {'title': 'No auth'}, {})
    assert response.status_code == 401
    assert 'error' in response.get_json()
    assert 'Bearer' in response.headers['WWW-Authenticate']


def test_a_bogus_token_is_rejected(client, db, world):
    response = post(client, {'title': 'x'}, {'Authorization': 'Bearer not-a-real-token'})
    assert response.status_code == 401
    assert WorkOrder.query.count() == 0


def test_the_x_api_key_header_also_works(client, db, world, token):
    response = post(client, {'title': 'Via header'}, {'X-API-Key': token})
    assert response.status_code == 201


def test_a_revoked_token_stops_working(client, db, world, token):
    ApiToken.query.delete()
    db.session.commit()
    assert post(client, {'title': 'x'}, {'Authorization': f'Bearer {token}'}).status_code == 401


def test_a_deactivated_user_cannot_use_their_token(client, db, world, token):
    user = User.query.filter_by(username='robot').one()
    user.is_active = False
    db.session.commit()
    assert post(client, {'title': 'x'}, {'Authorization': f'Bearer {token}'}).status_code == 401


def test_only_the_hash_is_stored(db, app):
    user = make_user('hashed')
    record, raw = ApiToken.issue(user, 'Somewhere')
    db.session.commit()
    assert record.token_hash != raw
    assert len(record.token_hash) == 64


def test_use_is_recorded(client, db, world, auth, token):
    assert ApiToken.query.one().last_used_at is None
    post(client, {'title': 'Track me'}, auth)
    assert ApiToken.query.one().last_used_at is not None


# ── creating ───────────────────────────────────────────────────────────────

def test_minimal_payload(client, db, world, auth):
    response = post(client, {'title': 'Leaking tap'}, auth)
    assert response.status_code == 201

    body = response.get_json()
    assert body['wo_number'].startswith('WO-')
    assert body['title'] == 'Leaking tap'
    assert body['status'] == 'open'
    assert body['priority'] == 'medium'
    assert response.headers['Location'].endswith(f"/api/v1/work-orders/{body['wo_number']}")


def test_full_payload_by_number(client, db, world, auth):
    response = post(client, {
        'title': 'Annual service',
        'asset_number': world['asset'].asset_number,
        'location_number': world['location'].location_number,
        'priority': 'high',
        'type': 'planned',
        'due_date': '2026-12-01',
        'description': 'From the API',
        'overdue_grace_days': 3,
    }, auth)
    assert response.status_code == 201

    body = response.get_json()
    assert body['asset_number'] == world['asset'].asset_number
    assert body['location_number'] == world['location'].location_number
    assert body['priority'] == 'high'
    assert body['type'] == 'planned'
    assert body['due_date'] == '2026-12-01'
    assert body['overdue_grace_days'] == 3


def test_location_is_inherited_from_the_asset(client, db, world, auth):
    """Same convenience the work order form gives you."""
    response = post(client, {'title': 'Inherit', 'asset_number': world['asset'].asset_number}, auth)
    assert response.get_json()['location_number'] == world['location'].location_number


def test_the_creating_user_is_recorded(client, db, world, auth):
    body = post(client, {'title': 'Attributed'}, auth).get_json()
    wo = WorkOrder.query.filter_by(wo_number=body['wo_number']).one()
    assert wo.creator.username == 'robot'


def test_completed_status_gets_todays_date(client, db, world, auth):
    body = post(client, {'title': 'Already done', 'status': 'completed'}, auth).get_json()
    assert body['completed_date'] == date.today().isoformat()


# ── invalid input ──────────────────────────────────────────────────────────

def test_unknown_asset_number_is_rejected(client, db, world, auth):
    response = post(client, {'title': 'x', 'asset_number': 'AST-99999'}, auth)
    assert response.status_code == 400

    body = response.get_json()
    assert 'asset_number' in body['errors']
    assert 'AST-99999' in body['errors']['asset_number']
    assert WorkOrder.query.count() == 0


def test_unknown_location_number_is_rejected(client, db, world, auth):
    response = post(client, {'title': 'x', 'location_number': 'LOC-99999'}, auth)
    assert response.status_code == 400
    assert 'location_number' in response.get_json()['errors']


def test_every_problem_is_reported_at_once(client, db, world, auth):
    """One round trip should tell a client everything that is wrong."""
    response = post(client, {
        'title': '', 'asset_number': 'AST-99999', 'location_number': 'LOC-99999',
        'priority': 'catastrophic', 'due_date': 'next tuesday',
    }, auth)
    errors = response.get_json()['errors']
    assert set(errors) == {'title', 'asset_number', 'location_number', 'priority', 'due_date'}


def test_missing_title_is_rejected(client, db, world, auth):
    response = post(client, {'asset_number': world['asset'].asset_number}, auth)
    assert response.status_code == 400
    assert 'title' in response.get_json()['errors']


def test_an_overlong_title_is_rejected(client, db, world, auth):
    response = post(client, {'title': 'x' * 300}, auth)
    assert 'title' in response.get_json()['errors']


def test_bad_priority_is_rejected_with_the_allowed_values(client, db, world, auth):
    response = post(client, {'title': 'x', 'priority': 'urgent'}, auth)
    message = response.get_json()['errors']['priority']
    assert 'critical' in message and 'medium' in message


def test_malformed_date_is_rejected(client, db, world, auth):
    response = post(client, {'title': 'x', 'due_date': '01/12/2026'}, auth)
    assert 'YYYY-MM-DD' in response.get_json()['errors']['due_date']


def test_negative_grace_is_rejected(client, db, world, auth):
    assert 'overdue_grace_days' in post(
        client, {'title': 'x', 'overdue_grace_days': -5}, auth).get_json()['errors']


def test_unknown_assignee_is_rejected(client, db, world, auth):
    assert 'assigned_to' in post(
        client, {'title': 'x', 'assigned_to': 'nobody'}, auth).get_json()['errors']


def test_decommissioned_asset_is_refused(client, db, world, auth):
    """Matches the rule the UI enforces: retired records take no new work."""
    world['asset'].status = STATUS_DECOMMISSIONED
    db.session.commit()

    response = post(client, {'title': 'x', 'asset_number': world['asset'].asset_number}, auth)
    assert response.status_code == 400
    assert 'decommissioned' in response.get_json()['errors']['asset_number']


def test_inactive_location_is_refused(client, db, world, auth):
    world['location'].status = STATUS_INACTIVE
    db.session.commit()
    response = post(client, {'title': 'x', 'location_number': world['location'].location_number}, auth)
    assert 'inactive' in response.get_json()['errors']['location_number']


def test_non_json_body_is_rejected(client, db, world, auth):
    response = client.post('/api/v1/work-orders', data='title=x', headers=auth)
    assert response.status_code == 400
    assert 'JSON' in response.get_json()['error']


def test_a_json_array_is_rejected(client, db, world, auth):
    response = post(client, ['not', 'an', 'object'], auth)
    assert response.status_code == 400


def test_nothing_is_created_when_validation_fails(client, db, world, auth):
    post(client, {'title': 'x', 'asset_number': 'AST-99999'}, auth)
    assert WorkOrder.query.count() == 0


# ── reading back ───────────────────────────────────────────────────────────

def test_a_created_work_order_can_be_fetched(client, db, world, auth):
    number = post(client, {'title': 'Round trip'}, auth).get_json()['wo_number']
    response = client.get(f'/api/v1/work-orders/{number}', headers=auth)
    assert response.status_code == 200
    assert response.get_json()['title'] == 'Round trip'


def test_unknown_work_order_returns_404_json(client, db, world, auth):
    response = client.get('/api/v1/work-orders/WO-2026-99999', headers=auth)
    assert response.status_code == 404
    assert 'WO-2026-99999' in response.get_json()['error']


def test_listing_and_filtering(client, db, world, auth):
    create_work_order(title='Open one', status='open')
    create_work_order(title='Done one', status='completed')

    body = client.get('/api/v1/work-orders', headers=auth).get_json()
    assert body['count'] == 2

    body = client.get('/api/v1/work-orders?status=completed', headers=auth).get_json()
    assert body['count'] == 1
    assert body['work_orders'][0]['title'] == 'Done one'


def test_bad_filter_is_rejected(client, db, world, auth):
    assert client.get('/api/v1/work-orders?status=nonsense', headers=auth).status_code == 400


def test_assets_and_locations_are_discoverable(client, db, world, auth):
    """A client needs a way to learn the numbers it must reference."""
    assets = client.get('/api/v1/assets', headers=auth).get_json()
    assert assets['assets'][0]['asset_number'] == world['asset'].asset_number
    assert assets['assets'][0]['location_number'] == world['location'].location_number

    locations = client.get('/api/v1/locations', headers=auth).get_json()
    assert locations['locations'][0]['location_number'] == world['location'].location_number


# ── error shape ────────────────────────────────────────────────────────────

def test_unknown_api_path_returns_json_not_html(client, db, auth):
    response = client.get('/api/v1/nope', headers=auth)
    assert response.status_code == 404
    assert response.is_json


def test_wrong_method_returns_json(client, db, auth):
    response = client.delete('/api/v1/work-orders', headers=auth)
    assert response.status_code == 405
    assert response.is_json


def test_html_pages_still_return_html(client, db, user, login):
    login()
    response = client.get('/assets/999999')
    assert response.status_code == 404
    assert not response.is_json


# ── named tokens ───────────────────────────────────────────────────────────

def test_a_user_can_hold_several_named_tokens(db, app):
    """One per integration, so revoking one does not disturb the others."""
    user = make_user('multi')
    _, home_assistant = ApiToken.issue(user, 'Home Assistant')
    _, phone = ApiToken.issue(user, 'Phone shortcut')
    db.session.commit()

    assert user.api_tokens.count() == 2
    assert {t.name for t in user.api_tokens} == {'Home Assistant', 'Phone shortcut'}
    assert home_assistant != phone


def test_revoking_one_leaves_the_others_working(client, db, world, app):
    user = make_user('multi')
    keep_record, keep = ApiToken.issue(user, 'Keep me')
    drop_record, drop = ApiToken.issue(user, 'Drop me')
    db.session.commit()

    db.session.delete(drop_record)
    db.session.commit()

    assert post(client, {'title': 'still works'}, {'Authorization': f'Bearer {keep}'}).status_code == 201
    assert post(client, {'title': 'nope'}, {'Authorization': f'Bearer {drop}'}).status_code == 401


def test_token_names_are_recorded(db, app):
    user = make_user('named')
    record, _ = ApiToken.issue(user, 'Home Assistant')
    db.session.commit()
    assert record.name == 'Home Assistant'
    assert record.created_at is not None


def test_an_overlong_name_is_truncated(db, app):
    user = make_user('longname')
    record, _ = ApiToken.issue(user, 'x' * 200)
    db.session.commit()
    assert len(record.name) <= 80


# ── parent references in the listings ──────────────────────────────────────

def test_assets_report_their_parent(client, db, world, auth):
    parent = create_asset(name='HVAC System', location_id=world['location'].id)
    child = create_asset(name='Blower', parent_id=parent.id, location_id=world['location'].id)

    rows = {a['asset_number']: a for a in client.get('/api/v1/assets', headers=auth).get_json()['assets']}
    assert rows[child.asset_number]['parent_asset_number'] == parent.asset_number
    assert rows[child.asset_number]['parent_asset_name'] == 'HVAC System'
    assert rows[parent.asset_number]['parent_asset_number'] is None
    assert rows[child.asset_number]['path'] == 'HVAC System › Blower'


def test_locations_report_their_parent(client, db, world, auth):
    child = create_location(name='Utility Room', parent_id=world['location'].id)

    rows = {l['location_number']: l for l in
            client.get('/api/v1/locations', headers=auth).get_json()['locations']}
    assert rows[child.location_number]['parent_location_number'] == world['location'].location_number
    assert rows[child.location_number]['parent_location_name'] == 'Basement'
    assert rows[world['location'].location_number]['parent_location_number'] is None
    assert rows[child.location_number]['path'] == 'Basement › Utility Room'


def test_work_orders_name_their_asset_and_location(client, db, world, auth):
    body = post(client, {'title': 'Named refs',
                         'asset_number': world['asset'].asset_number}, auth).get_json()
    assert body['asset_name'] == 'Furnace'
    assert body['location_name'] == 'Basement'
