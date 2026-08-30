"""Asset numbers, and location names being unique per parent rather than globally."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.asset import Asset
from app.models.location import Location
from app.services import create_asset, create_work_order, sibling_name_taken
from tests.conftest import CSRF


# ── asset numbers ──────────────────────────────────────────────────────────

def test_numbers_are_sequential(db):
    a = create_asset(name='Water Heater')
    b = create_asset(name='Oven')
    assert (a.asset_number, b.asset_number) == ('AST-00001', 'AST-00002')


def test_numbers_are_unique(db):
    create_asset(name='Router')
    create_asset(name='Router')
    numbers = [a.asset_number for a in Asset.query.all()]
    assert len(set(numbers)) == 2


def test_duplicate_names_are_still_allowed(db):
    """Two assets may share a name; the number is what tells them apart."""
    a = create_asset(name='Router')
    b = create_asset(name='Router')
    assert a.name == b.name
    assert a.id != b.id and a.asset_number != b.asset_number


def test_number_collision_is_retried(db, monkeypatch):
    create_asset(name='First')
    taken = ['AST-00001', 'AST-00007']
    monkeypatch.setattr(Asset, 'generate_asset_number',
                        staticmethod(lambda: taken.pop(0) if taken else 'AST-09999'))
    assert create_asset(name='Racer').asset_number == 'AST-00007'


def test_asset_number_is_required(db):
    db.session.add(Asset(name='No number'))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_display_label_pairs_name_and_number(db):
    asset = create_asset(name='Router')
    assert asset.display_label == 'Router (AST-00001)'


def test_number_shown_on_list_and_detail(client, db, user, login):
    asset = create_asset(name='Router')
    login()
    assert 'AST-00001' in client.get('/assets/').get_data(as_text=True)
    assert 'AST-00001' in client.get(f'/assets/{asset.id}').get_data(as_text=True)


def test_picker_disambiguates_same_named_assets(client, db, user, login):
    """The whole point: two "Router"s must be distinguishable when choosing one."""
    basement = Location(name='Basement')
    office = Location(name='Office')
    db.session.add_all([basement, office])
    db.session.commit()
    create_asset(name='Router', location_id=basement.id)
    create_asset(name='Router', location_id=office.id)

    login()
    body = client.get('/work-orders/new').get_data(as_text=True)
    assert 'Router (AST-00001) — Basement' in body
    assert 'Router (AST-00002) — Office' in body


def test_creating_through_the_form_allocates_a_number(client, db, user, login):
    login()
    client.post('/assets/new', data={'name': 'Dishwasher', 'status': 'active', 'csrf_token': CSRF})
    assert Asset.query.one().asset_number == 'AST-00001'


# ── location names: unique per parent ──────────────────────────────────────

@pytest.fixture
def floors(db):
    house = Location(name='House')
    db.session.add(house)
    db.session.flush()
    basement = Location(name='Basement', parent_id=house.id)
    main = Location(name='Main Floor', parent_id=house.id)
    db.session.add_all([basement, main])
    db.session.commit()
    return dict(house=house, basement=basement, main=main)


def test_same_name_under_different_parents_is_allowed(client, db, floors, user, login):
    login()
    for parent in (floors['basement'].id, floors['main'].id):
        response = client.post('/locations/new', data={
            'name': 'Bathroom', 'parent_id': parent, 'status': 'active', 'csrf_token': CSRF,
        })
        assert response.status_code == 302

    assert Location.query.filter_by(name='Bathroom').count() == 2


def test_same_name_under_the_same_parent_is_rejected(client, db, floors, user, login):
    login()
    client.post('/locations/new', data={
        'name': 'Bathroom', 'parent_id': floors['basement'].id,
        'status': 'active', 'csrf_token': CSRF})
    response = client.post('/locations/new', data={
        'name': 'Bathroom', 'parent_id': floors['basement'].id,
        'status': 'active', 'csrf_token': CSRF})

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'already exists under' in body and 'Basement' in body
    assert Location.query.filter_by(name='Bathroom').count() == 1


def test_duplicate_top_level_names_are_rejected(client, db, floors, user, login):
    """NULLs compare as distinct in SQL, so this needs the COALESCE in the index."""
    login()
    response = client.post('/locations/new', data={
        'name': 'House', 'status': 'active', 'csrf_token': CSRF})
    assert response.status_code == 200
    assert 'already exists at the top level' in response.get_data(as_text=True)


def test_the_check_is_case_insensitive(client, db, floors, user, login):
    login()
    response = client.post('/locations/new', data={
        'name': 'bASEMENT', 'parent_id': floors['house'].id,
        'status': 'active', 'csrf_token': CSRF})
    assert response.status_code == 200
    assert 'already exists' in response.get_data(as_text=True)


def test_renaming_onto_a_sibling_is_rejected(client, db, floors, user, login):
    login()
    response = client.post(f"/locations/{floors['main'].id}/edit", data={
        'name': 'Basement', 'parent_id': floors['house'].id,
        'status': 'active', 'csrf_token': CSRF})
    assert response.status_code == 200
    assert floors['main'].name == 'Main Floor'


def test_a_location_can_keep_its_own_name_when_edited(client, db, floors, user, login):
    """The uniqueness check must not treat the record as clashing with itself."""
    login()
    response = client.post(f"/locations/{floors['basement'].id}/edit", data={
        'name': 'Basement', 'parent_id': floors['house'].id,
        'status': 'inactive', 'csrf_token': CSRF})
    assert response.status_code == 302
    assert floors['basement'].status == 'inactive'


def test_moving_under_a_parent_that_already_has_the_name_is_rejected(client, db, floors, user, login):
    login()
    client.post('/locations/new', data={
        'name': 'Storage', 'parent_id': floors['basement'].id,
        'status': 'active', 'csrf_token': CSRF})
    client.post('/locations/new', data={
        'name': 'Storage', 'parent_id': floors['main'].id,
        'status': 'active', 'csrf_token': CSRF})

    moving = Location.query.filter_by(name='Storage', parent_id=floors['main'].id).one()
    response = client.post(f'/locations/{moving.id}/edit', data={
        'name': 'Storage', 'parent_id': floors['basement'].id,
        'status': 'active', 'csrf_token': CSRF})

    assert response.status_code == 200
    assert moving.parent_id == floors['main'].id


def test_sibling_helper_ignores_the_record_itself(db, floors):
    assert sibling_name_taken(floors['basement'], 'Basement', floors['house'].id) is False
    assert sibling_name_taken(None, 'Basement', floors['house'].id) is True


def test_database_rejects_a_sibling_duplicate_even_without_the_form(db, floors):
    """The index is the real guard, not just the friendly form check."""
    db.session.add(Location(name='Basement', parent_id=floors['house'].id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
