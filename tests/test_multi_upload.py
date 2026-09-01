"""Attaching several files at once, everywhere files can be attached."""
import io

import pytest

from app.models.attachment import Attachment
from app.models.location import Location
from app.services import create_location, create_asset, create_work_order
from tests.conftest import CSRF


def f(name, content=b'x'):
    return (io.BytesIO(content), name)


@pytest.fixture
def targets(db, user):
    """One of every entity that accepts attachments."""
    from datetime import date
    from app.models.job_plan import JobPlan
    from app.models.pm import PM

    loc = create_location(name='Garage')
    plan = JobPlan(name='Checklist')
    db.session.add_all([loc, plan])
    db.session.flush()
    pm = PM(name='Annual', interval_days=365, next_due_date=date.today())
    db.session.add(pm)
    db.session.commit()

    asset = create_asset(name='Furnace')
    wo = create_work_order(title='Service')
    return {
        'location': (f'/locations/{loc.id}/attachments', 'location', loc.id),
        'asset': (f'/assets/{asset.id}/attachments', 'asset', asset.id),
        'job_plan': (f'/job-plans/{plan.id}/attachments', 'job_plan', plan.id),
        'pm': (f'/pms/{pm.id}/attachments', 'pm', pm.id),
        'work_order': (f'/work-orders/{wo.id}/attachments', 'work_order', wo.id),
    }


@pytest.mark.parametrize('entity', ['location', 'asset', 'job_plan', 'pm', 'work_order'])
def test_several_files_in_one_go(client, db, targets, user, login, entity):
    url, entity_type, entity_id = targets[entity]
    login()
    client.post(url, data={
        'file': [f('one.pdf'), f('two.pdf'), f('three.pdf')], 'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    saved = Attachment.query.filter_by(entity_type=entity_type, entity_id=entity_id).all()
    assert {a.original_filename for a in saved} == {'one.pdf', 'two.pdf', 'three.pdf'}


def test_a_single_file_still_takes_a_friendly_name(client, db, targets, user, login):
    url, entity_type, entity_id = targets['asset']
    login()
    client.post(url, data={
        'file': f('MAN-1.pdf'), 'display_name': 'Furnace manual', 'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    assert Attachment.query.one().display_name == 'Furnace manual'


def test_a_name_is_dropped_when_several_files_are_chosen(client, db, targets, user, login):
    """One label cannot describe a whole selection, so filenames win."""
    url, entity_type, entity_id = targets['asset']
    login()
    client.post(url, data={
        'file': [f('a.pdf'), f('b.pdf')], 'display_name': 'Ignored', 'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    saved = Attachment.query.all()
    assert len(saved) == 2
    assert all(a.display_name is None for a in saved)
    assert {a.label for a in saved} == {'a.pdf', 'b.pdf'}


def test_a_rejected_type_does_not_stop_the_rest(client, db, targets, user, login):
    url, entity_type, entity_id = targets['asset']
    login()
    client.post(url, data={
        'file': [f('good.pdf'), f('bad.exe'), f('also-good.png')], 'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    assert {a.original_filename for a in Attachment.query.all()} == {'good.pdf', 'also-good.png'}


def test_no_files_is_reported(client, db, targets, user, login):
    url, entity_type, entity_id = targets['asset']
    login()
    response = client.post(url, data={'csrf_token': CSRF},
                           content_type='multipart/form-data', follow_redirects=True)
    assert 'No file selected' in response.get_data(as_text=True)
    assert Attachment.query.count() == 0


def test_form_rows_accept_several_files_each(client, db, user, login):
    """The work order form's repeatable rows take a selection per row."""
    login()
    client.post('/work-orders/new', data={
        'title': 'Batch', 'status': 'open', 'attachment_count': '2',
        'attachment_0_file': [f('a.pdf'), f('b.pdf')],
        'attachment_1_file': f('c.pdf'), 'attachment_1_name': 'Third one',
        'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    saved = Attachment.query.all()
    assert {a.label for a in saved} == {'a.pdf', 'b.pdf', 'Third one'}


def test_job_plan_form_rows_accept_several_files(client, db, user, login):
    login()
    client.post('/job-plans/new', data={
        'name': 'Plan', 'csrf_token': CSRF, 'task_count': '0',
        'material_count': '0', 'tool_count': '0',
        'attachment_count': '1',
        'attachment_0_file': [f('x.pdf'), f('y.pdf')],
    }, content_type='multipart/form-data')

    assert Attachment.query.count() == 2


def test_upload_control_offers_multiple(client, db, targets, user, login):
    login()
    body = client.get(f"/assets/{targets['asset'][2]}").get_data(as_text=True)
    assert 'type="file" name="file" multiple' in body
