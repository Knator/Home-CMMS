"""Entering a completion date on an existing work order completes it.

Recording when a job finished while leaving it open is a contradiction nobody
means, so the status follows the date. The rule is about the date *appearing*,
not about it being present — see the cancel case below.
"""
from datetime import date, timedelta

import pytest

from app.models.work_order import WorkOrder
from app.services import create_work_order
from app.extensions import db as _db
from tests.conftest import CSRF, make_user, prime_csrf


@pytest.fixture
def signed_in(client, db, login):
    make_user('tester', role='admin', password='Password123!')
    login('tester', 'Password123!')
    prime_csrf(client)
    return client


def save(client, wo, **fields):
    data = {
        'csrf_token': CSRF, 'title': wo.title, 'wo_type': wo.wo_type,
        'status': wo.status, 'priority': wo.priority,
    }
    data.update(fields)
    return client.post(f'/work-orders/{wo.id}/edit', data=data,
                       follow_redirects=True)


def test_entering_a_date_completes_the_work_order(signed_in, db):
    wo = create_work_order(title='Fix tap', status='open')
    save(signed_in, wo, status='open',
         completed_date=date.today().isoformat())

    _db.session.refresh(wo)
    assert wo.status == 'completed'
    assert wo.completed_date == date.today()


def test_it_says_so_rather_than_changing_things_silently(signed_in, db):
    wo = create_work_order(title='Fix tap', status='open')
    body = save(signed_in, wo, status='open',
                completed_date=date.today().isoformat()).get_data(as_text=True)
    assert 'Marked completed' in body


def test_a_backdated_completion_is_kept_as_typed(signed_in, db):
    """The date entered is the date the work was done, not today."""
    when = date.today() - timedelta(days=9)
    wo = create_work_order(title='Done last week', status='in_progress')
    save(signed_in, wo, status='in_progress', completed_date=when.isoformat())

    _db.session.refresh(wo)
    assert (wo.status, wo.completed_date) == ('completed', when)


def test_a_completed_work_order_can_still_be_cancelled(signed_in, db):
    """The reason the rule watches for the date appearing rather than being
    present. A completed work order keeps its date when cancelled, so reacting
    to mere presence would snap the status back on every save and make
    cancelling impossible."""
    wo = create_work_order(title='Called off after the fact', status='completed',
                           completed_date=date.today() - timedelta(days=2))
    save(signed_in, wo, status='cancelled',
         completed_date=wo.completed_date.isoformat())

    _db.session.refresh(wo)
    assert wo.status == 'cancelled'
    assert wo.completed_date is not None, 'the completion record was lost'


def test_clearing_the_date_does_not_complete_anything(signed_in, db):
    wo = create_work_order(title='Still going', status='in_progress')
    save(signed_in, wo, status='in_progress', completed_date='')

    _db.session.refresh(wo)
    assert wo.status == 'in_progress'
    assert wo.completed_date is None


def test_an_explicit_status_still_applies_when_no_date_is_added(signed_in, db):
    wo = create_work_order(title='Parked', status='open')
    save(signed_in, wo, status='on_hold', completed_date='')

    _db.session.refresh(wo)
    assert wo.status == 'on_hold'


def test_auto_completion_rolls_materials_onto_the_asset(signed_in, db):
    """Completing by date must do everything completing by status does. The
    roll-up is guarded by `status == 'completed' and not was_completed`, so it
    only fires if the status is set before that check runs — a reordering would
    lose it silently, with the work order looking correctly completed."""
    from app.models.asset_material import AssetMaterial
    from app.models.work_order_item import WorkOrderItem
    from app.services import create_asset

    asset = create_asset(name='Water Heater')
    wo = create_work_order(title='Flush it', status='open', asset_id=asset.id)
    _db.session.add(WorkOrderItem(work_order_id=wo.id, kind='material',
                                  sequence=1, description='Anode rod',
                                  part_number='AR-4471', quantity='1'))
    _db.session.commit()

    assert AssetMaterial.query.count() == 0
    # asset_id has to be posted back: the edit route treats an absent field as
    # cleared, so omitting it unlinks the asset and there is nothing to roll on.
    save(signed_in, wo, status='open', asset_id=str(asset.id),
         completed_date=date.today().isoformat(),
         material_count='1', material_0_description='Anode rod',
         material_0_part_number='AR-4471', material_0_quantity='1')

    _db.session.refresh(wo)
    assert wo.status == 'completed'
    assert AssetMaterial.query.count() == 1, 'the material never reached the asset'


# ── the same rule on the way in ────────────────────────────────────────────
#
# Create and the API follow the edit form, so a work order cannot be born
# recording when it finished while claiming to be open. Neither needs the
# transition test the edit path uses: a new record has no earlier date to keep.

def test_creating_with_a_completion_date_completes_it(signed_in, db):
    when = date.today() - timedelta(days=3)
    signed_in.post('/work-orders/new', data={
        'csrf_token': CSRF, 'title': 'Logged after the fact',
        'wo_type': 'unplanned', 'status': 'open', 'priority': 'medium',
        'completed_date': when.isoformat(),
    }, follow_redirects=True)

    wo = WorkOrder.query.filter_by(title='Logged after the fact').one()
    assert (wo.status, wo.completed_date) == ('completed', when)


def test_creating_without_one_keeps_the_status_asked_for(signed_in, db):
    signed_in.post('/work-orders/new', data={
        'csrf_token': CSRF, 'title': 'Still to do', 'wo_type': 'unplanned',
        'status': 'open', 'priority': 'medium', 'completed_date': '',
    }, follow_redirects=True)

    wo = WorkOrder.query.filter_by(title='Still to do').one()
    assert wo.status == 'open'
    assert wo.completed_date is None
