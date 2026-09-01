"""Parent/child trees and the lifecycle rules that protect work history."""
import pytest

from app.models.asset import Asset
from app.models.location import Location
from app.models.mixins import STATUS_ACTIVE, STATUS_INACTIVE, STATUS_DECOMMISSIONED
from app.services import (
create_location, create_asset,     asset_delete_blockers, location_delete_blockers, hierarchy_ordered,
    selectable_assets, selectable_locations, create_work_order,
)
from tests.conftest import CSRF


@pytest.fixture
def tree(db):
    """House > Basement > Utility Room, with a Furnace holding a Blower Motor."""
    house = create_location(name='House')
    db.session.add(house)
    db.session.flush()
    basement = create_location(name='Basement', parent_id=house.id)
    db.session.add(basement)
    db.session.flush()
    utility = create_location(name='Utility Room', parent_id=basement.id)
    db.session.add(utility)
    db.session.flush()

    furnace = create_asset(name='Furnace', location_id=utility.id)
    blower = create_asset(name='Blower Motor', parent_id=furnace.id, location_id=utility.id)
    return dict(house=house, basement=basement, utility=utility,
                furnace=furnace, blower=blower)


# ── tree walking ───────────────────────────────────────────────────────────

def test_ancestors_run_from_nearest_parent_upwards(tree):
    assert [a.name for a in tree['utility'].ancestors] == ['Basement', 'House']
    assert tree['house'].ancestors == []


def test_descendants_collects_the_whole_subtree(tree):
    assert {d.name for d in tree['house'].descendants} == {'Basement', 'Utility Room'}
    assert tree['utility'].descendants == []


def test_path_label_reads_root_first(tree):
    assert tree['utility'].path_label == 'House › Basement › Utility Room'
    assert tree['blower'].path_label == 'Furnace › Blower Motor'


def test_depth(tree):
    assert tree['house'].depth == 0
    assert tree['utility'].depth == 2


def test_hierarchy_ordered_is_depth_first(tree):
    rows = hierarchy_ordered(Location.query.all())
    assert [(n.name, d) for n, d in rows] == [
        ('House', 0), ('Basement', 1), ('Utility Room', 2),
    ]


def test_hierarchy_ordered_promotes_orphans_to_roots(tree):
    """A filtered list must not hide nodes whose parent was filtered out."""
    rows = hierarchy_ordered([tree['utility']])
    assert [(n.name, d) for n, d in rows] == [('Utility Room', 0)]


# ── cycle prevention ───────────────────────────────────────────────────────

def test_node_cannot_be_its_own_parent(tree):
    assert tree['basement'].would_create_cycle(tree['basement']) is True


def test_node_cannot_be_parented_to_its_own_descendant(tree):
    assert tree['house'].would_create_cycle(tree['utility']) is True


def test_unrelated_parent_is_allowed(db, tree):
    garage = create_location(name='Garage')
    db.session.add(garage)
    db.session.commit()
    assert tree['house'].would_create_cycle(garage) is False


def test_edit_rejects_a_cycle_through_the_form(client, db, tree, user, login):
    login()
    response = client.post(f"/locations/{tree['house'].id}/edit", data={
        'name': 'House', 'parent_id': tree['utility'].id,
        'status': STATUS_ACTIVE, 'csrf_token': CSRF,
    })
    assert response.status_code == 200          # re-rendered with the error
    assert tree['house'].parent_id is None
    assert 'cannot also be its parent' in response.get_data(as_text=True)


def test_parent_picker_excludes_self_and_descendants(client, db, tree, user, login):
    login()
    body = client.get(f"/locations/{tree['basement'].id}/edit").get_data(as_text=True)
    assert 'House' in body                       # valid parent
    assert f'value="{tree["basement"].id}"' not in body   # itself
    assert f'value="{tree["utility"].id}"' not in body    # its child


# ── deletion guards ────────────────────────────────────────────────────────

def test_location_with_children_cannot_be_deleted(tree):
    assert 'child location' in ' '.join(location_delete_blockers(tree['house']))


def test_location_with_assets_cannot_be_deleted(tree):
    assert any('asset' in b for b in location_delete_blockers(tree['utility']))


def test_location_with_work_orders_cannot_be_deleted(db, tree):
    create_work_order(title='Fix light', location_id=tree['basement'].id)
    blockers = location_delete_blockers(tree['basement'])
    assert any('work order' in b for b in blockers)


def test_empty_location_can_be_deleted(client, db, user, login):
    spare = create_location(name='Spare Room')
    db.session.add(spare)
    db.session.commit()
    assert location_delete_blockers(spare) == []

    login()
    response = client.post(f'/locations/{spare.id}/delete', data={'csrf_token': CSRF})
    assert response.status_code == 302
    assert Location.query.filter_by(name='Spare Room').first() is None


def test_delete_route_refuses_and_explains(client, db, tree, user, login):
    login()
    response = client.post(f"/locations/{tree['house'].id}/delete",
                           data={'csrf_token': CSRF}, follow_redirects=True)
    body = response.get_data(as_text=True)
    assert db.session.get(Location, tree['house'].id) is not None
    assert 'cannot be deleted' in body
    assert 'Decommissioned' in body


def test_asset_with_work_orders_cannot_be_deleted(db, tree):
    create_work_order(title='Service furnace', asset_id=tree['furnace'].id)
    assert any('work order' in b for b in asset_delete_blockers(tree['furnace']))


def test_asset_with_children_cannot_be_deleted(tree):
    assert any('child asset' in b for b in asset_delete_blockers(tree['furnace']))


def test_asset_delete_route_is_blocked(client, db, tree, user, login):
    login()
    client.post(f"/assets/{tree['furnace'].id}/delete", data={'csrf_token': CSRF})
    assert db.session.get(Asset, tree['furnace'].id) is not None


# ── lifecycle status ───────────────────────────────────────────────────────

def test_new_records_default_to_active(db):
    loc = create_location(name='New Place')
    db.session.add(loc)
    db.session.commit()
    assert loc.status == STATUS_ACTIVE
    assert loc.is_operational


def test_pickers_offer_active_only(db, tree):
    tree['basement'].status = STATUS_INACTIVE
    tree['utility'].status = STATUS_DECOMMISSIONED
    db.session.commit()

    names = {loc.name for loc in selectable_locations()}
    assert names == {'House'}


def test_picker_keeps_the_value_a_record_already_uses(db, tree):
    """Otherwise editing an old work order would silently blank its location."""
    tree['basement'].status = STATUS_DECOMMISSIONED
    db.session.commit()

    names = {loc.name for loc in selectable_locations(include_id=tree['basement'].id)}
    assert 'Basement' in names


def test_editing_a_wo_on_a_retired_asset_keeps_the_link(client, db, tree, user, login):
    wo = create_work_order(title='Old job', asset_id=tree['furnace'].id)
    tree['furnace'].status = STATUS_DECOMMISSIONED
    db.session.commit()

    login()
    body = client.get(f'/work-orders/{wo.id}/edit').get_data(as_text=True)
    assert 'Furnace' in body

    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': 'Old job', 'status': 'open', 'asset_id': tree['furnace'].id,
        'csrf_token': CSRF,
    })
    assert wo.asset_id == tree['furnace'].id


def test_status_survives_a_forged_value(client, db, tree, user, login):
    login()
    client.post(f"/locations/{tree['house'].id}/edit", data={
        'name': 'House', 'status': 'exploded', 'csrf_token': CSRF,
    })
    assert tree['house'].status == STATUS_ACTIVE


def test_list_hides_non_active_unless_asked(client, db, tree, user, login):
    tree['basement'].status = STATUS_DECOMMISSIONED
    db.session.commit()
    login()

    assert 'Basement' not in client.get('/locations/').get_data(as_text=True)
    assert 'Basement' in client.get('/locations/?show=all').get_data(as_text=True)
