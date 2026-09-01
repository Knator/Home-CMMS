"""Attachments are polymorphic with no foreign key, so deletes must clean up."""
import io
import os

import pytest

from app.models.attachment import Attachment
from app.services import create_location, create_asset
from app.utils import entity_upload_dir
from tests.conftest import CSRF


def upload(client, url, filename='manual.pdf', content=b'%PDF-1.4 fake'):
    return client.post(url, data={
        'file': (io.BytesIO(content), filename), 'csrf_token': CSRF,
    }, content_type='multipart/form-data')


@pytest.fixture
def asset(db):
    return create_asset(name='Water Heater')


def test_upload_then_download(client, app, asset, user, login):
    login()
    upload(client, f'/assets/{asset.id}/attachments')

    att = Attachment.query.filter_by(entity_type='asset', entity_id=asset.id).one()
    assert att.original_filename == 'manual.pdf'
    assert os.path.exists(os.path.join(entity_upload_dir('asset', asset.id), att.stored_filename))

    response = client.get(f'/attachments/{att.id}/download')
    assert response.status_code == 200
    assert response.data == b'%PDF-1.4 fake'


def test_disallowed_extension_is_rejected(client, asset, user, login):
    login()
    upload(client, f'/assets/{asset.id}/attachments', filename='payload.exe')
    assert Attachment.query.count() == 0


def test_deleting_an_asset_purges_its_attachments(client, app, db, asset, user, login):
    login()
    upload(client, f'/assets/{asset.id}/attachments')
    upload_dir = entity_upload_dir('asset', asset.id)
    assert os.path.isdir(upload_dir)

    client.post(f'/assets/{asset.id}/delete', data={'csrf_token': CSRF})

    assert Attachment.query.filter_by(entity_type='asset', entity_id=asset.id).count() == 0
    assert not os.path.exists(upload_dir)


def test_deleting_a_work_order_purges_its_attachments(client, app, db, user, login):
    from app.services import create_asset, create_work_order

    login()
    wo = create_work_order(title='Leaky tap', created_by=user.id)
    upload(client, f'/work-orders/{wo.id}/attachments')
    upload_dir = entity_upload_dir('work_order', wo.id)

    client.post(f'/work-orders/{wo.id}/delete', data={'csrf_token': CSRF})

    assert Attachment.query.count() == 0
    assert not os.path.exists(upload_dir)


def test_deleting_a_location_purges_its_attachments(client, app, db, user, login):
    from app.models.location import Location

    loc = create_location(name='Garage')
    db.session.add(loc)
    db.session.commit()

    login()
    upload(client, f'/locations/{loc.id}/attachments')
    client.post(f'/locations/{loc.id}/delete', data={'csrf_token': CSRF})

    assert Attachment.query.count() == 0


def test_filenames_are_stored_uniquely(client, asset, user, login):
    login()
    upload(client, f'/assets/{asset.id}/attachments', content=b'first')
    upload(client, f'/assets/{asset.id}/attachments', content=b'second')

    stored = [a.stored_filename for a in Attachment.query.all()]
    assert len(stored) == 2
    assert len(set(stored)) == 2
    assert all(name.endswith('_manual.pdf') for name in stored)
