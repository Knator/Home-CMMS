"""Materials, tools, duration totals, ordering and form attachments."""
import io

import pytest

from app.models.job_plan import JobPlan, JobPlanItem, JobPlanTask
from app.models.attachment import Attachment
from app.utils import format_duration
from tests.conftest import CSRF


def create_plan(client, **extra):
    data = {
        'name': 'Flush Water Heater', 'csrf_token': CSRF,
        'task_count': '2',
        'task_0_description': 'Turn off power', 'task_0_minutes': '5',
        'task_1_description': 'Drain tank', 'task_1_minutes': '40',
        'material_count': '2',
        'material_0_description': 'Garden hose', 'material_0_quantity': '1',
        'material_1_description': 'Teflon tape', 'material_1_quantity': '2 rolls',
        'tool_count': '1',
        'tool_0_description': 'Adjustable wrench',
    }
    data.update(extra)
    return client.post('/job-plans/new', data=data, content_type='multipart/form-data')


# ── materials and tools ────────────────────────────────────────────────────

def test_materials_and_tools_are_saved(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()

    assert [(m.description, m.quantity) for m in plan.materials] == [
        ('Garden hose', '1'), ('Teflon tape', '2 rolls'),
    ]
    assert [t.description for t in plan.tools] == ['Adjustable wrench']


def test_materials_and_tools_are_separate(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()
    assert len(plan.materials) == 2
    assert len(plan.tools) == 1
    assert JobPlanItem.query.count() == 3


def test_quantity_is_optional(client, db, user, login):
    login()
    create_plan(client, material_count='1', material_0_description='Rag',
                material_0_quantity='')
    assert JobPlan.query.one().materials[0].quantity is None


def test_blank_rows_are_skipped_without_gaps(client, db, user, login):
    login()
    create_plan(client, material_count='3',
                material_0_description='First', material_1_description='   ',
                material_2_description='Third')
    assert [m.sequence for m in JobPlan.query.one().materials] == [1, 2]


def test_editing_replaces_materials_and_tools(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()

    client.post(f'/job-plans/{plan.id}/edit', data={
        'name': plan.name, 'csrf_token': CSRF,
        'task_count': '0', 'material_count': '1',
        'material_0_description': 'Only this', 'tool_count': '0',
    }, content_type='multipart/form-data')

    assert [m.description for m in plan.materials] == ['Only this']
    assert plan.tools == []
    assert JobPlanItem.query.count() == 1


def test_deleting_a_plan_removes_its_items(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()
    client.post(f'/job-plans/{plan.id}/delete', data={'csrf_token': CSRF})
    assert JobPlanItem.query.count() == 0


def test_item_count_is_capped(client, db, user, login):
    """A forged count must not loop; only rows carrying a description are saved."""
    login()
    create_plan(client, material_count='999999',
                material_0_description='One', material_1_description='')
    assert len(JobPlan.query.one().materials) == 1


def test_detail_page_shows_materials_and_tools(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()
    body = client.get(f'/job-plans/{plan.id}').get_data(as_text=True)

    assert 'Required Materials (2)' in body
    assert 'Required Tools (1)' in body
    assert 'Teflon tape' in body and '2 rolls' in body
    assert 'Adjustable wrench' in body


# ── duration ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('minutes,expected', [
    (0, '—'), (None, '—'), (5, '5m'), (60, '1h'), (95, '1h 35m'), (600, '10h'),
])
def test_duration_formatting(minutes, expected):
    assert format_duration(minutes) == expected


def test_total_is_the_sum_of_task_estimates(client, db, user, login):
    login()
    create_plan(client)
    assert JobPlan.query.one().total_minutes == 45


def test_tasks_without_an_estimate_count_as_zero(client, db, user, login):
    login()
    create_plan(client, task_count='2',
                task_0_description='Timed', task_0_minutes='30',
                task_1_description='Untimed', task_1_minutes='')
    assert JobPlan.query.one().total_minutes == 30


def test_total_shown_on_detail_and_list(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()

    assert '45m' in client.get(f'/job-plans/{plan.id}').get_data(as_text=True)
    listing = client.get('/job-plans/').get_data(as_text=True)
    assert '>Duration</th>' in listing
    assert '45m' in listing


def test_plan_with_no_estimates_shows_a_dash(client, db, user, login):
    login()
    create_plan(client, task_count='1', task_0_description='No estimate', task_0_minutes='')
    assert JobPlan.query.one().total_minutes == 0


# ── ordering ───────────────────────────────────────────────────────────────

def test_sequence_follows_submitted_order(client, db, user, login):
    """Reordering is done in the browser; the server just reads DOM order."""
    login()
    create_plan(client, task_count='3',
                task_0_description='Was third', task_1_description='Was first',
                task_2_description='Was second')

    plan = JobPlan.query.one()
    assert [t.description for t in plan.tasks] == ['Was third', 'Was first', 'Was second']
    assert [t.sequence for t in plan.tasks] == [1, 2, 3]


def test_reordering_on_edit_rewrites_the_sequence(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()
    assert [t.description for t in plan.tasks] == ['Turn off power', 'Drain tank']

    client.post(f'/job-plans/{plan.id}/edit', data={
        'name': plan.name, 'csrf_token': CSRF, 'task_count': '2',
        'task_0_description': 'Drain tank', 'task_0_minutes': '40',
        'task_1_description': 'Turn off power', 'task_1_minutes': '5',
        'material_count': '0', 'tool_count': '0',
    }, content_type='multipart/form-data')

    assert [t.description for t in plan.tasks] == ['Drain tank', 'Turn off power']
    assert [t.sequence for t in plan.tasks] == [1, 2]


def test_form_rows_carry_the_reorder_controls(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()
    body = client.get(f'/job-plans/{plan.id}/edit').get_data(as_text=True)

    assert 'draggable="true"' in body
    assert 'drag-handle' in body
    assert 'data-direction="up"' in body      # keyboard fallback for dragging
    assert 'data-direction="down"' in body


# ── attachments move to the form ───────────────────────────────────────────

def test_files_can_be_attached_while_creating(client, db, user, login):
    login()
    create_plan(client, attachment_count='1',
                attachment_0_file=(io.BytesIO(b'%PDF'), 'guide.pdf'),
                attachment_0_name='Flush guide')

    plan = JobPlan.query.one()
    att = Attachment.query.filter_by(entity_type='job_plan', entity_id=plan.id).one()
    assert att.label == 'Flush guide'


def test_files_can_be_attached_while_editing(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()

    client.post(f'/job-plans/{plan.id}/edit', data={
        'name': plan.name, 'csrf_token': CSRF,
        'task_count': '0', 'material_count': '0', 'tool_count': '0',
        'attachment_count': '1',
        'attachment_0_file': (io.BytesIO(b'%PDF'), 'spec.pdf'),
    }, content_type='multipart/form-data')

    assert Attachment.query.count() == 1


def test_detail_page_no_longer_uploads(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()
    body = client.get(f'/job-plans/{plan.id}').get_data(as_text=True)

    assert 'upload-form' not in body
    assert f'/job-plans/{plan.id}/edit' in body      # points at the form instead


def test_form_offers_attachment_rows(client, db, user, login):
    login()
    body = client.get('/job-plans/new').get_data(as_text=True)
    assert 'enctype="multipart/form-data"' in body
    assert 'attachment-rows' in body
