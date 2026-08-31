"""Image attachments get a cached thumbnail and a full-size view."""
import io
import os

import pytest

from app.models.attachment import Attachment
from app.services import create_asset, create_work_order
from app.utils import thumbnail_path
from tests.conftest import CSRF

Image = pytest.importorskip('PIL.Image', reason='Pillow generates the thumbnails')


def photo_bytes(size=(900, 600), fmt='JPEG', colour=(200, 30, 30)):
    """A real image, big enough that thumbnailing is a visible reduction."""
    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.new('RGB', size, colour).save(buf, fmt)
    buf.seek(0)
    return buf.read()


def upload(client, asset, name='photo.jpg', data=None):
    return client.post(f'/assets/{asset.id}/attachments', data={
        'file': (io.BytesIO(data or photo_bytes()), name), 'csrf_token': CSRF,
    }, content_type='multipart/form-data')


@pytest.fixture
def asset(db, user):
    return create_asset(name='Furnace')


# ── which files count as images ────────────────────────────────────────────

def test_image_attachments_are_recognised(client, db, asset, user, login):
    login()
    upload(client, asset, 'photo.jpg')
    assert Attachment.query.one().is_image is True


def test_documents_are_not(client, db, asset, user, login):
    login()
    client.post(f'/assets/{asset.id}/attachments',
                data={'file': (io.BytesIO(b'%PDF'), 'manual.pdf'), 'csrf_token': CSRF},
                content_type='multipart/form-data')
    assert Attachment.query.one().is_image is False


# ── the thumbnail itself ───────────────────────────────────────────────────

def test_thumbnail_is_generated_and_smaller(client, app, db, asset, user, login):
    login()
    original = photo_bytes(size=(1600, 1200))
    upload(client, asset, 'big.jpg', data=original)
    att = Attachment.query.one()

    response = client.get(f'/attachments/{att.id}/thumbnail')
    assert response.status_code == 200
    assert len(response.data) < len(original)

    from PIL import Image as PILImage
    thumb = PILImage.open(io.BytesIO(response.data))
    assert max(thumb.size) <= 320


def test_thumbnail_is_cached_on_disk(client, app, db, asset, user, login):
    login()
    upload(client, asset)
    att = Attachment.query.one()

    with app.app_context():
        assert not os.path.exists(thumbnail_path(att.id))
    client.get(f'/attachments/{att.id}/thumbnail')
    with app.app_context():
        assert os.path.exists(thumbnail_path(att.id))


def test_second_request_reuses_the_cache(client, app, db, asset, user, login):
    login()
    upload(client, asset)
    att = Attachment.query.one()

    first = client.get(f'/attachments/{att.id}/thumbnail').data
    with app.app_context():
        mtime = os.path.getmtime(thumbnail_path(att.id))
    second = client.get(f'/attachments/{att.id}/thumbnail').data
    with app.app_context():
        assert os.path.getmtime(thumbnail_path(att.id)) == mtime   # not rebuilt
    assert first == second


def test_thumbnails_are_cacheable_by_the_browser(client, db, asset, user, login):
    login()
    upload(client, asset)
    att = Attachment.query.one()
    response = client.get(f'/attachments/{att.id}/thumbnail')
    assert 'max-age' in response.headers['Cache-Control']
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_png_with_transparency_is_handled(client, db, asset, user, login):
    """RGBA cannot be saved as JPEG directly; it must be flattened first."""
    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.new('RGBA', (500, 500), (0, 128, 0, 128)).save(buf, 'PNG')
    login()
    upload(client, asset, 'transparent.png', data=buf.getvalue())
    att = Attachment.query.one()

    assert client.get(f'/attachments/{att.id}/thumbnail').status_code == 200


def test_a_corrupt_image_falls_back_to_the_original(client, db, asset, user, login):
    """A broken file must not 500 the page it appears on."""
    login()
    upload(client, asset, 'broken.jpg', data=b'this is not an image')
    att = Attachment.query.one()

    response = client.get(f'/attachments/{att.id}/thumbnail')
    assert response.status_code == 200
    assert response.data == b'this is not an image'


def test_documents_have_no_thumbnail(client, db, asset, user, login):
    login()
    client.post(f'/assets/{asset.id}/attachments',
                data={'file': (io.BytesIO(b'%PDF'), 'manual.pdf'), 'csrf_token': CSRF},
                content_type='multipart/form-data')
    att = Attachment.query.one()
    assert client.get(f'/attachments/{att.id}/thumbnail').status_code == 404


def test_thumbnail_requires_login(client, db, asset, user, login):
    login()
    upload(client, asset)
    att_id = Attachment.query.one().id
    client.get('/auth/logout')

    response = client.get(f'/attachments/{att_id}/thumbnail')
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']


# ── cleanup ────────────────────────────────────────────────────────────────

def test_deleting_an_attachment_drops_its_thumbnail(client, app, db, asset, user, login):
    login()
    upload(client, asset)
    att = Attachment.query.one()
    client.get(f'/attachments/{att.id}/thumbnail')
    with app.app_context():
        path = thumbnail_path(att.id)
    assert os.path.exists(path)

    client.post(f'/attachments/{att.id}/delete', data={'csrf_token': CSRF})
    assert not os.path.exists(path)


def test_deleting_the_owner_drops_thumbnails_too(client, app, db, user, login):
    """purge_entity_attachments must clear the cache, which lives outside the
    entity's upload directory."""
    login()
    victim = create_asset(name='Doomed')
    upload(client, victim)
    att = Attachment.query.one()
    client.get(f'/attachments/{att.id}/thumbnail')
    with app.app_context():
        path = thumbnail_path(att.id)
    assert os.path.exists(path)

    client.post(f'/assets/{victim.id}/delete', data={'csrf_token': CSRF})
    assert not os.path.exists(path)


# ── how it appears ─────────────────────────────────────────────────────────

def test_images_render_a_thumbnail_linking_to_the_full_size(client, db, asset, user, login):
    login()
    upload(client, asset)
    att = Attachment.query.one()

    body = client.get(f'/assets/{asset.id}').get_data(as_text=True)
    assert f'/attachments/{att.id}/thumbnail' in body      # the preview
    assert f'/attachments/{att.id}/inline' in body         # full size, viewed not downloaded
    assert 'data-lightbox' in body


def test_documents_show_an_extension_chip_instead(client, db, asset, user, login):
    login()
    client.post(f'/assets/{asset.id}/attachments',
                data={'file': (io.BytesIO(b'%PDF'), 'manual.pdf'), 'csrf_token': CSRF},
                content_type='multipart/form-data')

    body = client.get(f'/assets/{asset.id}').get_data(as_text=True)
    assert 'attachment-icon' in body
    assert 'PDF' in body


def test_the_link_still_works_without_javascript(client, db, asset, user, login):
    """The lightbox is an enhancement; the anchor must open the image regardless."""
    login()
    upload(client, asset)
    att = Attachment.query.one()

    body = client.get(f'/assets/{asset.id}').get_data(as_text=True)
    assert f'href="/attachments/{att.id}/inline"' in body

    response = client.get(f'/attachments/{att.id}/inline')
    assert response.status_code == 200
    assert 'attachment' not in response.headers.get('Content-Disposition', '')


def test_related_documents_show_previews_too(client, db, user, login):
    login()
    asset = create_asset(name='Furnace')
    upload(client, asset)
    wo = create_work_order(title='Service', asset_id=asset.id)

    body = client.get(f'/work-orders/{wo.id}').get_data(as_text=True)
    assert 'attachment-thumb' in body


# ── behaviour when Pillow is unavailable ───────────────────────────────────

def test_large_originals_are_never_served_as_thumbnails(client, db, asset, user, login, monkeypatch):
    """The bug this guards: a 13 MB photo sent to fill a 48px box stalled
    scrolling for seconds. No preview is better than that."""
    import app.attachments.routes as routes

    login()
    big = photo_bytes(size=(4000, 3000))
    upload(client, asset, 'huge.jpg', data=big)
    att = Attachment.query.one()

    monkeypatch.setattr(routes, 'build_thumbnail', lambda *a, **k: None)
    monkeypatch.setattr(routes, 'THUMBNAIL_FALLBACK_MAX_BYTES', 1024)

    assert client.get(f'/attachments/{att.id}/thumbnail').status_code == 404


def test_small_originals_may_still_stand_in(client, db, asset, user, login, monkeypatch):
    import app.attachments.routes as routes

    login()
    upload(client, asset, 'tiny.jpg', data=photo_bytes(size=(40, 40)))
    att = Attachment.query.one()

    monkeypatch.setattr(routes, 'build_thumbnail', lambda *a, **k: None)
    assert client.get(f'/attachments/{att.id}/thumbnail').status_code == 200


def test_availability_reflects_whether_pillow_imports(monkeypatch):
    import sys
    from app.utils import thumbnails_available

    assert thumbnails_available() is True
    # sys.modules[name] = None makes `import name` raise ImportError.
    monkeypatch.setitem(sys.modules, 'PIL', None)
    assert thumbnails_available() is False


def test_templates_skip_previews_without_pillow(monkeypatch, tmp_path):
    """Otherwise every list would request full-size images it cannot shrink.

    Built as a separate app because Jinja binds an imported macro's globals once
    (make_module is shared), so the flag cannot be flipped mid-process.
    """
    import io
    import app as app_pkg
    from app import create_app
    from app.extensions import db as _db
    from app.models.user import User
    from app.services import create_asset

    monkeypatch.setattr(app_pkg, 'thumbnails_available', lambda: False)

    application = create_app(config_overrides={
        'TESTING': True, 'SECRET_KEY': 'test-secret',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/t.db',
        'UPLOAD_FOLDER': str(tmp_path / 'up'), 'SCHEDULER_ENABLED': False,
    })
    assert application.jinja_env.globals['thumbnails_available'] is False

    with application.app_context():
        _db.create_all()
        u = User(username='t', email='t@example.com', role='user')
        u.set_password('password123')
        _db.session.add(u)
        _db.session.commit()
        asset_id = create_asset(name='Furnace').id

    c = application.test_client()
    with c.session_transaction() as sess:
        sess['csrf_token'] = CSRF
    c.post('/auth/login', data={'username': 't', 'password': 'password123', 'csrf_token': CSRF})
    with c.session_transaction() as sess:
        sess['csrf_token'] = CSRF

    c.post(f'/assets/{asset_id}/attachments',
           data={'file': (io.BytesIO(photo_bytes()), 'p.jpg'), 'csrf_token': CSRF},
           content_type='multipart/form-data')

    body = c.get(f'/assets/{asset_id}').get_data(as_text=True)
    assert 'attachment-thumb' not in body      # no full-size image requested
    assert 'attachment-icon' in body           # extension chip instead

    with application.app_context():
        att = Attachment.query.one()
    # Full-size viewing is unaffected; only the preview is skipped.
    assert c.get(f'/attachments/{att.id}/inline').status_code == 200


def test_very_large_images_still_thumbnail(client, app, db, asset, user, login):
    """Phone panorama modes exceed Pillow's default 179 MP bomb guard; real
    uploads were being rejected by it."""
    from PIL import Image as PILImage

    login()
    # Comfortably past Pillow's default limit, small enough to build in a test.
    huge = photo_bytes(size=(14000, 13000))
    upload(client, asset, 'panorama.jpg', data=huge)
    att = Attachment.query.one()

    response = client.get(f'/attachments/{att.id}/thumbnail')
    assert response.status_code == 200
    thumb = PILImage.open(io.BytesIO(response.data))
    assert max(thumb.size) <= 320


def test_the_bomb_guard_is_raised_not_removed(app):
    """Bounded, so a genuinely absurd file is still refused."""
    from app.utils import THUMBNAIL_MAX_PIXELS
    assert THUMBNAIL_MAX_PIXELS is not None
    assert THUMBNAIL_MAX_PIXELS > 179_000_000
