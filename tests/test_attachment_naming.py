"""Friendly names, and attaching files straight from the work order form."""
import io

import pytest

from app.models.attachment import Attachment
from app.models.asset import Asset
from app.models.location import Location
from app.models.work_order import WorkOrder
from app.services import create_work_order
from tests.conftest import CSRF


def f(name='manual.pdf', content=b'data'):
    return (io.BytesIO(content), name)


# ── friendly names ─────────────────────────────────────────────────────────

def test_upload_accepts_a_friendly_name(client, db, user, login):
    login()
    wo = create_work_order(title='Job')
    client.post(f'/work-orders/{wo.id}/attachments', data={
        'file': f('MAN-4471-rev-c.pdf'), 'display_name': 'Furnace manual',
        'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    att = Attachment.query.one()
    assert att.display_name == 'Furnace manual'
    assert att.original_filename == 'MAN-4471-rev-c.pdf'
    assert att.label == 'Furnace manual'


def test_label_falls_back_to_the_filename(client, db, user, login):
    login()
    wo = create_work_order(title='Job')
    client.post(f'/work-orders/{wo.id}/attachments',
                data={'file': f('receipt.pdf'), 'csrf_token': CSRF},
                content_type='multipart/form-data')
    assert Attachment.query.one().label == 'receipt.pdf'


def test_rename_sets_and_clears(client, db, user, login):
    login()
    wo = create_work_order(title='Job')
    client.post(f'/work-orders/{wo.id}/attachments',
                data={'file': f('scan001.pdf'), 'csrf_token': CSRF},
                content_type='multipart/form-data')
    att = Attachment.query.one()

    client.post(f'/attachments/{att.id}/rename',
                data={'display_name': 'Warranty card', 'csrf_token': CSRF})
    assert att.display_name == 'Warranty card'

    client.post(f'/attachments/{att.id}/rename', data={'display_name': '', 'csrf_token': CSRF})
    assert att.display_name is None
    assert att.label == 'scan001.pdf'


def test_rename_returns_to_the_owning_entity(client, db, user, login):
    login()
    loc = Location(name='Garage')
    db.session.add(loc)
    db.session.commit()
    client.post(f'/locations/{loc.id}/attachments',
                data={'file': f('plan.pdf'), 'csrf_token': CSRF},
                content_type='multipart/form-data')
    att = Attachment.query.one()

    response = client.post(f'/attachments/{att.id}/rename',
                           data={'display_name': 'Floor plan', 'csrf_token': CSRF})
    assert response.headers['Location'] == f'/locations/{loc.id}'


def test_rename_requires_csrf(client, db, user, login):
    login()
    wo = create_work_order(title='Job')
    client.post(f'/work-orders/{wo.id}/attachments',
                data={'file': f(), 'csrf_token': CSRF}, content_type='multipart/form-data')
    att = Attachment.query.one()
    assert client.post(f'/attachments/{att.id}/rename',
                       data={'display_name': 'x'}).status_code == 403


def test_download_uses_the_friendly_name_and_keeps_the_extension(client, db, user, login):
    login()
    wo = create_work_order(title='Job')
    client.post(f'/work-orders/{wo.id}/attachments', data={
        'file': f('MAN-4471.pdf'), 'display_name': 'Furnace manual', 'csrf_token': CSRF,
    }, content_type='multipart/form-data')
    att = Attachment.query.one()

    assert att.download_name == 'Furnace manual.pdf'
    disposition = client.get(f'/attachments/{att.id}/download').headers['Content-Disposition']
    assert 'Furnace manual.pdf' in disposition


def test_download_name_does_not_double_the_extension(db):
    att = Attachment(entity_type='asset', entity_id=1, stored_filename='x_a.pdf',
                     original_filename='a.pdf', display_name='Guide.pdf')
    assert att.download_name == 'Guide.pdf'


def test_friendly_name_is_shown_with_the_real_filename(client, db, user, login):
    login()
    wo = create_work_order(title='Job')
    client.post(f'/work-orders/{wo.id}/attachments', data={
        'file': f('MAN-4471.pdf'), 'display_name': 'Furnace manual', 'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    body = client.get(f'/work-orders/{wo.id}').get_data(as_text=True)
    assert 'Furnace manual' in body
    assert 'MAN-4471.pdf' in body          # the real filename stays visible


def test_roll_up_shows_friendly_names(client, db, user, login):
    login()
    asset = Asset(name='Furnace')
    db.session.add(asset)
    db.session.commit()
    client.post(f'/assets/{asset.id}/attachments', data={
        'file': f('MAN-4471.pdf'), 'display_name': 'Furnace manual', 'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    wo = create_work_order(title='Service', asset_id=asset.id)
    assert 'Furnace manual' in client.get(f'/work-orders/{wo.id}').get_data(as_text=True)


# ── attaching from the work order form ─────────────────────────────────────

def test_files_can_be_attached_while_creating(client, db, user, login):
    login()
    response = client.post('/work-orders/new', data={
        'title': 'Leaky tap', 'status': 'open',
        'attachment_count': '2',
        'attachment_0_file': f('quote.pdf'), 'attachment_0_name': 'Plumber quote',
        'attachment_1_file': f('photo.jpg'), 'attachment_1_name': '',
        'csrf_token': CSRF,
    }, content_type='multipart/form-data')
    assert response.status_code == 302

    wo = WorkOrder.query.one()
    atts = Attachment.query.filter_by(entity_type='work_order', entity_id=wo.id).all()
    assert {a.label for a in atts} == {'Plumber quote', 'photo.jpg'}


def test_files_can_be_attached_while_editing(client, db, user, login):
    login()
    wo = create_work_order(title='Job')
    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': 'Job', 'status': 'open',
        'attachment_count': '1',
        'attachment_0_file': f('invoice.pdf'), 'attachment_0_name': 'Final invoice',
        'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    assert Attachment.query.one().label == 'Final invoice'


def test_editing_without_files_changes_nothing(client, db, user, login):
    login()
    wo = create_work_order(title='Job')
    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': 'Renamed', 'status': 'open', 'attachment_count': '0', 'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    assert wo.title == 'Renamed'
    assert Attachment.query.count() == 0


def test_a_rejected_file_does_not_discard_the_work_order(client, db, user, login):
    """One bad extension must not lose the form submission or the good files."""
    login()
    client.post('/work-orders/new', data={
        'title': 'Mixed batch', 'status': 'open',
        'attachment_count': '2',
        'attachment_0_file': f('payload.exe'),
        'attachment_1_file': f('good.pdf'),
        'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    wo = WorkOrder.query.filter_by(title='Mixed batch').one()
    labels = {a.label for a in Attachment.query.all()}
    assert labels == {'good.pdf'}
    assert wo is not None


def test_forged_attachment_count_is_capped(client, db, user, login):
    login()
    response = client.post('/work-orders/new', data={
        'title': 'Huge', 'status': 'open', 'attachment_count': '999999',
        'attachment_0_file': f('one.pdf'), 'csrf_token': CSRF,
    }, content_type='multipart/form-data')
    assert response.status_code == 302
    assert Attachment.query.count() == 1


def test_empty_file_rows_are_skipped(client, db, user, login):
    login()
    client.post('/work-orders/new', data={
        'title': 'Sparse', 'status': 'open', 'attachment_count': '3',
        'attachment_1_file': f('only.pdf'), 'csrf_token': CSRF,
    }, content_type='multipart/form-data')
    assert Attachment.query.count() == 1


# ── asset -> location inheritance endpoint ─────────────────────────────────

def test_asset_summary_returns_its_location(client, db, user, login):
    house = Location(name='House')
    db.session.add(house)
    db.session.flush()
    basement = Location(name='Basement', parent_id=house.id)
    db.session.add(basement)
    db.session.flush()
    furnace = Asset(name='Furnace', location_id=basement.id)
    db.session.add(furnace)
    db.session.commit()

    login()
    data = client.get(f'/assets/{furnace.id}/summary').get_json()
    assert data['location_id'] == basement.id
    assert data['location_name'] == 'Basement'
    assert data['location_path'] == 'House › Basement'


def test_asset_summary_handles_no_location(client, db, user, login):
    asset = Asset(name='Spare')
    db.session.add(asset)
    db.session.commit()

    login()
    data = client.get(f'/assets/{asset.id}/summary').get_json()
    assert data['location_id'] is None
    assert data['location_name'] is None


def test_asset_summary_requires_login(client, db):
    asset_id = 1
    response = client.get(f'/assets/{asset_id}/summary')
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']


def test_asset_summary_404s_for_unknown_asset(client, db, user, login):
    login()
    assert client.get('/assets/9999/summary').status_code == 404
