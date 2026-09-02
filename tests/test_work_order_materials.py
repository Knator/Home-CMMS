"""Materials and tools on work orders, and the roll-up onto assets."""
from datetime import date, timedelta

import pytest

from app.models.asset_material import AssetMaterial
from app.models.job_plan import JobPlan, JobPlanItem
from app.models.work_order import WorkOrder
from app.models.work_order_item import WorkOrderItem
from app.services import create_asset, create_work_order, record_materials_on_asset
from tests.conftest import CSRF


@pytest.fixture
def plan(db):
    plan = JobPlan(name='Flush Water Heater')
    db.session.add(plan)
    db.session.flush()
    db.session.add_all([
        JobPlanItem(job_plan_id=plan.id, kind='material', sequence=1,
                    description='Anode rod', part_number='AR-4471', quantity='1'),
        JobPlanItem(job_plan_id=plan.id, kind='material', sequence=2,
                    description='Teflon tape', quantity='2 rolls'),
        JobPlanItem(job_plan_id=plan.id, kind='tool', sequence=1,
                    description='Adjustable wrench'),
    ])
    db.session.commit()
    return plan


@pytest.fixture
def asset(db, user):
    return create_asset(name='Water Heater')


def wo_form(**extra):
    data = {'title': 'Job', 'status': 'open', 'csrf_token': CSRF}
    data.update(extra)
    return data


# ── copying from the job plan ──────────────────────────────────────────────

def test_creating_with_a_job_plan_copies_its_items(client, db, plan, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(job_plan_id=plan.id),
                content_type='multipart/form-data')

    wo = WorkOrder.query.one()
    assert [m.description for m in wo.materials] == ['Anode rod', 'Teflon tape']
    assert [t.description for t in wo.tools] == ['Adjustable wrench']


def test_the_part_number_comes_across(client, db, plan, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(job_plan_id=plan.id),
                content_type='multipart/form-data')
    assert WorkOrder.query.one().materials[0].part_number == 'AR-4471'


def test_the_copy_is_a_snapshot_not_a_live_link(client, db, plan, user, login):
    """Editing the job plan afterwards must not rewrite work already raised."""
    login()
    client.post('/work-orders/new', data=wo_form(job_plan_id=plan.id),
                content_type='multipart/form-data')
    wo = WorkOrder.query.one()

    for item in plan.items.all():
        db.session.delete(item)
    db.session.commit()

    assert len(wo.materials) == 2       # untouched


def test_attaching_a_plan_later_brings_its_items(client, db, plan, user, login):
    login()
    wo = create_work_order(title='No plan yet')
    client.post(f'/work-orders/{wo.id}/edit',
                data=wo_form(title='No plan yet', job_plan_id=plan.id),
                content_type='multipart/form-data')
    assert len(wo.materials) == 2


def test_existing_lines_are_never_overwritten_by_the_plan(client, db, plan, user, login):
    """Someone edited the list for this job; re-copying would discard that."""
    login()
    client.post('/work-orders/new', data=wo_form(
        material_count='1', material_0_description='Something I chose myself',
        tool_count='0', job_plan_id=plan.id), content_type='multipart/form-data')

    wo = WorkOrder.query.one()
    assert [m.description for m in wo.materials] == ['Something I chose myself']


# ── editing directly, with no job plan ─────────────────────────────────────

def test_materials_can_be_added_without_a_job_plan(client, db, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(
        material_count='2',
        material_0_description='Copper elbow', material_0_part_number='CE-12',
        material_0_quantity='2',
        material_1_description='Solder', material_1_quantity='1 roll',
        tool_count='1', tool_0_description='Blowtorch',
    ), content_type='multipart/form-data')

    wo = WorkOrder.query.one()
    assert wo.job_plan_id is None
    assert [(m.description, m.part_number, m.quantity) for m in wo.materials] == [
        ('Copper elbow', 'CE-12', '2'), ('Solder', None, '1 roll')]
    assert [t.description for t in wo.tools] == ['Blowtorch']


def test_editing_replaces_the_lists(client, db, user, login):
    login()
    wo = create_work_order(title='Job')
    client.post(f'/work-orders/{wo.id}/edit', data=wo_form(
        material_count='1', material_0_description='First', tool_count='0'),
        content_type='multipart/form-data')
    client.post(f'/work-orders/{wo.id}/edit', data=wo_form(
        material_count='1', material_0_description='Replaced', tool_count='0'),
        content_type='multipart/form-data')

    assert [m.description for m in wo.materials] == ['Replaced']
    assert WorkOrderItem.query.count() == 1


def test_blank_rows_are_skipped(client, db, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(
        material_count='3', material_0_description='Real',
        material_1_description='   ', material_2_description='Also real',
        tool_count='0'), content_type='multipart/form-data')
    assert [m.sequence for m in WorkOrder.query.one().materials] == [1, 2]


def test_deleting_a_work_order_removes_its_items(client, db, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(
        material_count='1', material_0_description='Gone soon', tool_count='0'),
        content_type='multipart/form-data')
    wo = WorkOrder.query.one()

    client.post(f'/work-orders/{wo.id}/delete', data={'csrf_token': CSRF})
    assert WorkOrderItem.query.count() == 0


# ── roll-up onto the asset ─────────────────────────────────────────────────

def test_completing_records_materials_on_the_asset(client, db, asset, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(
        asset_id=asset.id, status='completed', completed_date='2026-05-01',
        material_count='1', material_0_description='Anode rod',
        material_0_part_number='AR-4471', material_0_quantity='1', tool_count='0'),
        content_type='multipart/form-data')

    recorded = AssetMaterial.query.one()
    assert recorded.asset_id == asset.id
    assert recorded.part_number == 'AR-4471'
    assert recorded.times_used == 1
    assert recorded.last_used_on == date(2026, 5, 1)


def test_nothing_is_recorded_until_completion(client, db, asset, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(
        asset_id=asset.id, status='open',
        material_count='1', material_0_description='Anode rod', tool_count='0'),
        content_type='multipart/form-data')
    assert AssetMaterial.query.count() == 0


def test_completing_later_records_them(client, db, asset, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(
        asset_id=asset.id, material_count='1', material_0_description='Anode rod',
        tool_count='0'), content_type='multipart/form-data')
    wo = WorkOrder.query.one()

    client.post(f'/work-orders/{wo.id}/edit', data=wo_form(
        title=wo.title, asset_id=asset.id, status='completed',
        material_count='1', material_0_description='Anode rod', tool_count='0'),
        content_type='multipart/form-data')

    assert AssetMaterial.query.count() == 1


def test_resaving_a_completed_work_order_does_not_count_twice(client, db, asset, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(
        asset_id=asset.id, status='completed',
        material_count='1', material_0_description='Anode rod', tool_count='0'),
        content_type='multipart/form-data')
    wo = WorkOrder.query.one()

    for _ in range(3):
        client.post(f'/work-orders/{wo.id}/edit', data=wo_form(
            title=wo.title, asset_id=asset.id, status='completed',
            material_count='1', material_0_description='Anode rod', tool_count='0'),
            content_type='multipart/form-data')

    assert AssetMaterial.query.one().times_used == 1


def test_the_same_part_used_again_updates_one_row(client, db, asset, user, login):
    login()
    for when in ('2026-01-10', '2026-06-20'):
        client.post('/work-orders/new', data=wo_form(
            asset_id=asset.id, status='completed', completed_date=when,
            material_count='1', material_0_description='Anode rod',
            material_0_part_number='AR-4471', tool_count='0'),
            content_type='multipart/form-data')

    recorded = AssetMaterial.query.one()
    assert recorded.times_used == 2
    assert recorded.first_used_on == date(2026, 1, 10)
    assert recorded.last_used_on == date(2026, 6, 20)


def test_matching_is_by_part_number_when_present(db, asset):
    """The same part described differently is still the same part."""
    first = create_work_order(title='One', asset_id=asset.id, status='completed',
                             completed_date=date(2026, 1, 1))
    db.session.add(WorkOrderItem(work_order_id=first.id, kind='material', sequence=1,
                                 description='Anode rod', part_number='AR-4471'))
    db.session.commit()
    record_materials_on_asset(first)

    second = create_work_order(title='Two', asset_id=asset.id, status='completed',
                               completed_date=date(2026, 2, 1))
    db.session.add(WorkOrderItem(work_order_id=second.id, kind='material', sequence=1,
                                 description='anode (magnesium)', part_number='AR-4471'))
    db.session.commit()
    record_materials_on_asset(second)
    db.session.commit()

    assert AssetMaterial.query.count() == 1
    assert AssetMaterial.query.one().times_used == 2


def test_a_part_number_learned_later_fills_in(db, asset):
    first = create_work_order(title='One', asset_id=asset.id, status='completed',
                              completed_date=date(2026, 1, 1))
    db.session.add(WorkOrderItem(work_order_id=first.id, kind='material', sequence=1,
                                 description='Anode rod'))
    db.session.commit()
    record_materials_on_asset(first)
    db.session.commit()
    assert AssetMaterial.query.one().part_number is None

    second = create_work_order(title='Two', asset_id=asset.id, status='completed',
                               completed_date=date(2026, 2, 1))
    db.session.add(WorkOrderItem(work_order_id=second.id, kind='material', sequence=1,
                                 description='Anode rod', part_number='AR-4471'))
    db.session.commit()
    record_materials_on_asset(second)
    db.session.commit()

    assert AssetMaterial.query.one().part_number == 'AR-4471'


def test_tools_are_not_rolled_up(client, db, asset, user, login):
    """Only consumables belong on the asset's parts list."""
    login()
    client.post('/work-orders/new', data=wo_form(
        asset_id=asset.id, status='completed',
        material_count='0', tool_count='1', tool_0_description='Blowtorch'),
        content_type='multipart/form-data')
    assert AssetMaterial.query.count() == 0


def test_a_work_order_with_no_asset_records_nothing(client, db, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(
        status='completed', material_count='1', material_0_description='Anode rod',
        tool_count='0'), content_type='multipart/form-data')
    assert AssetMaterial.query.count() == 0


def test_the_part_survives_the_work_order_being_deleted(client, db, asset, user, login):
    """The whole point is looking this up long after the job."""
    login()
    client.post('/work-orders/new', data=wo_form(
        asset_id=asset.id, status='completed',
        material_count='1', material_0_description='Anode rod',
        material_0_part_number='AR-4471', tool_count='0'),
        content_type='multipart/form-data')
    wo = WorkOrder.query.one()

    client.post(f'/work-orders/{wo.id}/delete', data={'csrf_token': CSRF})

    recorded = AssetMaterial.query.one()
    assert recorded.part_number == 'AR-4471'
    assert recorded.last_work_order_id is None      # reference cleared, part kept


def test_deleting_the_asset_removes_its_materials(client, db, asset, user, login):
    login()
    db.session.add(AssetMaterial(asset_id=asset.id, description='Anode rod'))
    db.session.commit()

    client.post(f'/assets/{asset.id}/delete', data={'csrf_token': CSRF})
    assert AssetMaterial.query.count() == 0


# ── how it appears ─────────────────────────────────────────────────────────

def test_asset_page_lists_the_parts(client, db, asset, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(
        asset_id=asset.id, status='completed',
        material_count='1', material_0_description='Anode rod',
        material_0_part_number='AR-4471', tool_count='0'),
        content_type='multipart/form-data')

    body = client.get(f'/assets/{asset.id}').get_data(as_text=True)
    assert 'Materials Used (1)' in body
    assert 'AR-4471' in body


def test_asset_page_explains_an_empty_list(client, db, asset, user, login):
    login()
    assert 'No materials recorded yet' in client.get(f'/assets/{asset.id}').get_data(as_text=True)


def test_work_order_page_shows_its_lists(client, db, plan, user, login):
    login()
    client.post('/work-orders/new', data=wo_form(job_plan_id=plan.id),
                content_type='multipart/form-data')
    wo = WorkOrder.query.one()

    body = client.get(f'/work-orders/{wo.id}').get_data(as_text=True)
    assert 'Materials &amp; Tools' in body
    assert 'Anode rod' in body
    assert 'AR-4471' in body
    assert 'Adjustable wrench' in body


def test_two_different_part_numbers_stay_separate(db, asset):
    """However similarly described, two numbers mean two parts."""
    for number, when in (('AR-4471', date(2026, 1, 1)), ('AR-9900', date(2026, 2, 1))):
        wo = create_work_order(title=f'Job {number}', asset_id=asset.id,
                               status='completed', completed_date=when)
        db.session.add(WorkOrderItem(work_order_id=wo.id, kind='material', sequence=1,
                                     description='Anode rod', part_number=number))
        db.session.commit()
        record_materials_on_asset(wo)
        db.session.commit()

    assert AssetMaterial.query.count() == 2


def test_the_same_part_described_differently_without_numbers_stays_separate(db, asset):
    """With nothing but free text to go on, we cannot safely merge."""
    for text in ('Anode rod', 'Magnesium anode'):
        wo = create_work_order(title=text, asset_id=asset.id, status='completed',
                               completed_date=date(2026, 1, 1))
        db.session.add(WorkOrderItem(work_order_id=wo.id, kind='material', sequence=1,
                                     description=text))
        db.session.commit()
        record_materials_on_asset(wo)
        db.session.commit()

    assert AssetMaterial.query.count() == 2


def test_matching_ignores_case_and_padding(db, asset):
    for text, number in (('Anode rod', 'AR-4471'), ('  anode ROD  ', ' ar-4471 ')):
        wo = create_work_order(title='Job', asset_id=asset.id, status='completed',
                               completed_date=date(2026, 1, 1))
        db.session.add(WorkOrderItem(work_order_id=wo.id, kind='material', sequence=1,
                                     description=text, part_number=number))
        db.session.commit()
        record_materials_on_asset(wo)
        db.session.commit()

    assert AssetMaterial.query.count() == 1
