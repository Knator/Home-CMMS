"""Work orders surface documents held on the things they're associated with."""
import io

import pytest

from app.models.asset import Asset
from app.models.job_plan import JobPlan
from app.models.location import Location
from app.models.pm import PM
from app.services import create_asset, create_work_order, related_attachments
from tests.conftest import CSRF


@pytest.fixture
def world(db, user):
    """House > Basement holding a Furnace, plus a job plan and a PM."""
    house = Location(name='House')
    db.session.add(house)
    db.session.flush()
    basement = Location(name='Basement', parent_id=house.id)
    db.session.add(basement)
    db.session.flush()

    hvac = create_asset(name='HVAC System', location_id=basement.id)
    furnace = create_asset(name='Furnace', parent_id=hvac.id, location_id=basement.id)
    plan = JobPlan(name='Filter change')
    db.session.add(plan)
    db.session.flush()

    from datetime import date
    pm = PM(name='Annual service', asset_id=furnace.id, job_plan_id=plan.id,
            interval_days=365, next_due_date=date.today())
    db.session.add(pm)
    db.session.commit()
    return dict(house=house, basement=basement, hvac=hvac, furnace=furnace,
                plan=plan, pm=pm)


def attach(client, url, filename):
    return client.post(url, data={'file': (io.BytesIO(b'x'), filename), 'csrf_token': CSRF},
                       content_type='multipart/form-data')


def test_rolls_up_from_every_association(client, db, world, user, login):
    login()
    attach(client, f"/pms/{world['pm'].id}/attachments", 'pm-checklist.pdf')
    attach(client, f"/job-plans/{world['plan'].id}/attachments", 'steps.pdf')
    attach(client, f"/assets/{world['furnace'].id}/attachments", 'furnace-manual.pdf')
    attach(client, f"/assets/{world['hvac'].id}/attachments", 'system-diagram.pdf')
    attach(client, f"/locations/{world['basement'].id}/attachments", 'basement-plan.pdf')
    attach(client, f"/locations/{world['house'].id}/attachments", 'shutoff-map.pdf')

    wo = create_work_order(title='Service', asset_id=world['furnace'].id,
                           location_id=world['basement'].id,
                           job_plan_id=world['plan'].id, pm_id=world['pm'].id)

    got = {r['attachment'].original_filename: r['source_label'] for r in related_attachments(wo)}
    assert got == {
        'pm-checklist.pdf': 'PM',
        'steps.pdf': 'Job Plan',
        'furnace-manual.pdf': 'Asset',
        'system-diagram.pdf': 'Parent asset',
        'basement-plan.pdf': 'Location',
        'shutoff-map.pdf': 'Parent location',
    }


def test_names_the_source_record(client, db, world, user, login):
    login()
    attach(client, f"/assets/{world['hvac'].id}/attachments", 'system-diagram.pdf')
    wo = create_work_order(title='Service', asset_id=world['furnace'].id)

    row = next(r for r in related_attachments(wo)
               if r['attachment'].original_filename == 'system-diagram.pdf')
    assert row['source_name'] == 'HVAC System'


def test_falls_back_to_the_assets_location(client, db, world, user, login):
    """A work order with an asset but no location still gets the location's files."""
    login()
    attach(client, f"/locations/{world['basement'].id}/attachments", 'basement-plan.pdf')
    wo = create_work_order(title='No location set', asset_id=world['furnace'].id)

    names = [r['attachment'].original_filename for r in related_attachments(wo)]
    assert 'basement-plan.pdf' in names


def test_returns_nothing_without_associations(db, user):
    wo = create_work_order(title='Standalone')
    assert related_attachments(wo) == []


def test_the_work_orders_own_files_are_not_duplicated(client, db, world, user, login):
    login()
    wo = create_work_order(title='Service', asset_id=world['furnace'].id)
    attach(client, f'/work-orders/{wo.id}/attachments', 'photo.jpg')

    names = [r['attachment'].original_filename for r in related_attachments(wo)]
    assert 'photo.jpg' not in names


def test_detail_page_lists_related_documents(client, db, world, user, login):
    login()
    attach(client, f"/assets/{world['furnace'].id}/attachments", 'furnace-manual.pdf')
    wo = create_work_order(title='Service', asset_id=world['furnace'].id)

    body = client.get(f'/work-orders/{wo.id}').get_data(as_text=True)
    assert 'Related Documents' in body
    assert 'furnace-manual.pdf' in body
    assert 'Asset: Furnace' in body


def test_generated_work_order_inherits_pm_documents(client, db, world, user, login):
    """The headline case: a WO the scheduler creates carries the PM's paperwork."""
    login()
    attach(client, f"/pms/{world['pm'].id}/attachments", 'pm-checklist.pdf')
    attach(client, f"/job-plans/{world['plan'].id}/attachments", 'steps.pdf')

    from app.services import generate_work_order_for_pm
    wo = generate_work_order_for_pm(world['pm'])

    names = [r['attachment'].original_filename for r in related_attachments(wo)]
    assert 'pm-checklist.pdf' in names
    assert 'steps.pdf' in names


def test_links_point_at_the_original_file(client, db, world, user, login):
    """No copies: the roll-up links to the asset's own attachment row."""
    login()
    attach(client, f"/assets/{world['furnace'].id}/attachments", 'furnace-manual.pdf')
    wo = create_work_order(title='Service', asset_id=world['furnace'].id)

    row = related_attachments(wo)[0]['attachment']
    assert row.entity_type == 'asset'
    assert row.entity_id == world['furnace'].id

    assert client.get(f'/attachments/{row.id}/download').status_code == 200
