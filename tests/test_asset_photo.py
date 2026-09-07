"""The optional asset photo, and the asset number linking from the list."""
import io
import os

from app.models.attachment import Attachment
from app.models.asset import Asset
from app.services import create_asset
from app.utils import entity_upload_dir
from tests.conftest import CSRF

# A tiny valid PNG, so the bytes are a real image rather than arbitrary data.
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
    '890000000a49444154789c6360000002000100ffff03000006000557bfabd400'
    '00000049454e44ae426082'
)


def png(name='photo.png'):
    return (io.BytesIO(PNG), name)


def edit_asset(client, asset, **extra):
    """Post the asset edit form, carrying the fields it always submits."""
    data = {'name': asset.name, 'status': asset.status, 'csrf_token': CSRF}
    data.update(extra)
    return client.post(f'/assets/{asset.id}/edit', data=data,
                       content_type='multipart/form-data')


def upload_photo(client, asset, name='photo.png', caption=None):
    """Photos are set through the edit form, not a route of their own."""
    return edit_asset(client, asset, image=png(name), image_caption=caption or '')


def test_detail_page_has_no_photo_card_until_one_is_set(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    body = client.get(f'/assets/{asset.id}').get_data(as_text=True)
    assert '<span class="card-title">Photo</span>' not in body
    assert 'asset-photo' not in body


def test_photo_controls_live_on_the_edit_form_only(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    edit = client.get(f'/assets/{asset.id}/edit').get_data(as_text=True)
    assert 'name="image"' in edit
    assert 'enctype="multipart/form-data"' in edit

    detail = client.get(f'/assets/{asset.id}').get_data(as_text=True)
    assert 'name="image"' not in detail


def test_create_form_offers_a_photo(client, db, user, login):
    login()
    body = client.get('/assets/new').get_data(as_text=True)
    assert 'name="image"' in body
    assert 'enctype="multipart/form-data"' in body


def test_photo_can_be_set_while_creating(client, db, user, login):
    login()
    response = client.post('/assets/new', data={
        'name': 'Furnace', 'status': 'active',
        'image': png('new.png'), 'image_caption': 'On arrival',
        'csrf_token': CSRF,
    }, content_type='multipart/form-data')
    assert response.status_code == 302

    asset = Asset.query.one()
    assert asset.image is not None
    assert asset.image.original_filename == 'new.png'
    assert asset.image.display_name == 'On arrival'


def test_creating_without_a_photo_still_works(client, db, user, login):
    login()
    client.post('/assets/new', data={'name': 'Plain', 'status': 'active', 'csrf_token': CSRF},
                content_type='multipart/form-data')
    assert Asset.query.one().image_attachment_id is None


def test_a_bad_image_blocks_creation(client, db, user, login):
    """Validated before the insert, so no half-made asset is left behind."""
    login()
    response = client.post('/assets/new', data={
        'name': 'Furnace', 'status': 'active',
        'image': (io.BytesIO(b'%PDF'), 'manual.pdf'), 'csrf_token': CSRF,
    }, content_type='multipart/form-data')

    assert response.status_code == 200
    assert 'not an image' in response.get_data(as_text=True)
    assert Asset.query.count() == 0
    assert Attachment.query.count() == 0


def test_caption_can_be_edited_without_reuploading(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    upload_photo(client, asset, caption='Front')
    edit_asset(client, asset, image_caption='Back panel')
    assert asset.image.display_name == 'Back panel'


def test_a_rejected_image_leaves_the_asset_unchanged(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    upload_photo(client, asset, name='good.png')
    original = asset.image_attachment_id

    edit_asset(client, asset, name='Renamed', image=(io.BytesIO(b'%PDF'), 'bad.pdf'))

    db.session.refresh(asset)
    assert asset.image_attachment_id == original      # photo kept
    assert asset.name == 'Furnace'                    # rename rolled back too


def test_uploading_sets_the_photo(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    assert upload_photo(client, asset).status_code == 302

    assert asset.image_attachment_id is not None
    assert asset.image.original_filename == 'photo.png'


def test_photo_is_shown_on_the_detail_page(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    upload_photo(client, asset, caption='Front panel')

    body = client.get(f'/assets/{asset.id}').get_data(as_text=True)
    assert f'/attachments/{asset.image_attachment_id}/inline' in body
    assert 'Front panel' in body
    assert 'alt="Photo of Furnace"' in body


def test_inline_route_serves_the_image(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    upload_photo(client, asset)

    response = client.get(f'/attachments/{asset.image_attachment_id}/inline')
    assert response.status_code == 200
    assert response.data == PNG
    assert 'attachment' not in response.headers.get('Content-Disposition', '')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_inline_route_refuses_what_the_browser_cannot_display(client, db, user, login):
    """Inline is no longer images-only — a PDF is viewable, and so are video,
    audio and text. It is still a curated list, so a format with nothing to
    show is refused rather than rendered."""
    asset = create_asset(name='Furnace')
    login()
    for content, filename in ((b'%PDF', 'manual.pdf'), (b'DWG', 'layout.dwg')):
        client.post(f'/assets/{asset.id}/attachments',
                    data={'file': (io.BytesIO(content), filename), 'csrf_token': CSRF},
                    content_type='multipart/form-data')

    pdf = Attachment.query.filter_by(original_filename='manual.pdf').one()
    dwg = Attachment.query.filter_by(original_filename='layout.dwg').one()

    assert client.get(f'/attachments/{pdf.id}/inline').status_code == 200
    assert client.get(f'/attachments/{dwg.id}/inline').status_code == 404
    # Being viewable does not make it a photo: that is a separate check, and
    # test_non_image_upload_is_rejected covers it.
    assert not pdf.is_image


def test_inline_route_requires_login(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    upload_photo(client, asset)
    att_id = asset.image_attachment_id

    client.get('/auth/logout')
    response = client.get(f'/attachments/{att_id}/inline')
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']


def test_non_image_upload_is_rejected(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    edit_asset(client, asset, image=(io.BytesIO(b'%PDF'), 'manual.pdf'))

    assert asset.image_attachment_id is None
    assert Attachment.query.count() == 0


def test_replacing_removes_the_previous_photo(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    upload_photo(client, asset, name='first.png')
    first_id = asset.image_attachment_id
    first_path = os.path.join(entity_upload_dir('asset', asset.id),
                              db.session.get(Attachment, first_id).stored_filename)

    upload_photo(client, asset, name='second.png')

    assert asset.image.original_filename == 'second.png'
    assert db.session.get(Attachment, first_id) is None
    assert not os.path.exists(first_path)
    assert Attachment.query.count() == 1


def test_removing_the_photo(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    upload_photo(client, asset)

    edit_asset(client, asset, remove_image='1')
    assert asset.image_attachment_id is None
    assert Attachment.query.count() == 0


def test_deleting_the_attachment_clears_the_reference(client, db, user, login):
    """ON DELETE SET NULL: removing the file must not leave a dangling id."""
    asset = create_asset(name='Furnace')
    login()
    upload_photo(client, asset)
    att_id = asset.image_attachment_id

    client.post(f'/attachments/{att_id}/delete', data={'csrf_token': CSRF})
    db.session.refresh(asset)
    assert asset.image_attachment_id is None


def test_photo_upload_requires_csrf(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    response = client.post(f'/assets/{asset.id}/edit',
                           data={'name': asset.name, 'status': asset.status, 'image': png()},
                           content_type='multipart/form-data')
    assert response.status_code == 403


def test_photo_does_not_appear_on_other_pages(client, db, user, login):
    """The photo is asset-detail only, by design."""
    asset = create_asset(name='Furnace')
    login()
    upload_photo(client, asset)
    inline = f'/attachments/{asset.image_attachment_id}/inline'

    for path in ('/assets/', '/', '/work-orders/'):
        assert inline not in client.get(path).get_data(as_text=True), path


# ── asset number links from the list ───────────────────────────────────────

def test_asset_number_links_to_the_record(client, db, user, login):
    asset = create_asset(name='Furnace')
    login()
    body = client.get('/assets/').get_data(as_text=True)
    assert f'href="/assets/{asset.id}">AST-00001</a>' in body
