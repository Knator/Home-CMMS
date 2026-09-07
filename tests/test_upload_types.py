"""What may be attached, and how big.

Attachments are only ever handed back by the download route, which always sends
as_attachment=True, and the inline route serves IMAGE_EXTENSIONS alone. So the
allowlist is depth rather than the only defence — but it still must not accept
anything that would make the instance a convenient host for someone's malware.
"""
import io

import pytest

from app.models.attachment import Attachment
from app.services import create_work_order
from tests.conftest import CSRF, prime_csrf


@pytest.fixture
def signed_in(client, db, user, login):
    login()
    prime_csrf(client)
    return client


@pytest.fixture
def wo(db):
    return create_work_order(title='Pump is making a noise', wo_type='unplanned')


def upload(client, wo_id, filename, content=b'x'):
    return client.post(f'/work-orders/{wo_id}/attachments', data={
        'csrf_token': CSRF, 'file': (io.BytesIO(content), filename),
    }, content_type='multipart/form-data', follow_redirects=True)


# ── the formats that prompted this ─────────────────────────────────────────

@pytest.mark.parametrize('filename', [
    'pump-noise.mp4',      # the one that was refused
    'walkthrough.mov',
    'clip.webm',
    'recording.mkv',
    'old-camcorder.avi',
])
def test_video_can_be_attached(signed_in, wo, filename):
    upload(signed_in, wo.id, filename)
    assert Attachment.query.filter_by(original_filename=filename).count() == 1


@pytest.mark.parametrize('filename', [
    'basement.dwg', 'layout.dxf', 'bracket.step', 'bracket.stp',
    'mount.stl', 'shed.skp', 'part.f3d', 'housing.sldprt', 'plate.ipt',
    'thing.3mf', 'print.gcode',
])
def test_cad_and_3d_files_can_be_attached(signed_in, wo, filename):
    upload(signed_in, wo.id, filename)
    assert Attachment.query.filter_by(original_filename=filename).count() == 1


@pytest.mark.parametrize('filename', [
    'noise.mp3', 'voice-note.m4a', 'hum.wav',          # audio
    'photo.heic', 'scan.tiff', 'shot.dng',             # phone and raw images
    'budget.xls', 'slides.pptx', 'notes.md',           # more documents
    'logs.7z', 'export.json', 'manual.log',            # archives and data
])
def test_the_other_widened_formats_are_accepted(signed_in, wo, filename):
    upload(signed_in, wo.id, filename)
    assert Attachment.query.filter_by(original_filename=filename).count() == 1


# ── what stays out ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('filename', [
    'setup.exe', 'installer.msi', 'run.bat', 'go.cmd', 'script.ps1',
    'deploy.sh', 'applet.jar', 'payload.js', 'macro.vbs',
])
def test_executables_are_still_refused(signed_in, wo, filename):
    response = upload(signed_in, wo.id, filename)
    assert Attachment.query.filter_by(original_filename=filename).count() == 0
    assert b'not accepted' in response.data


@pytest.mark.parametrize('filename', ['page.html', 'page.htm', 'icon.svg', 'doc.xhtml'])
def test_renderable_markup_is_still_refused(signed_in, wo, filename):
    """SVG can carry script; so can HTML. Nothing renders an upload today, but
    the allowlist should not be the thing standing between that and a stored XSS."""
    upload(signed_in, wo.id, filename)
    assert Attachment.query.filter_by(original_filename=filename).count() == 0


def test_a_file_with_no_extension_is_refused_with_a_clear_reason(signed_in, wo):
    response = upload(signed_in, wo.id, 'README')
    assert Attachment.query.count() == 0
    assert b'no file extension' in response.data


def test_the_rejection_names_the_extension(signed_in, wo):
    """Listing the ~100 accepted types would be useless; naming the refused one
    is not."""
    response = upload(signed_in, wo.id, 'trojan.exe')
    assert b'.exe files are not accepted' in response.data


# ── previewable is narrower than allowed ───────────────────────────────────

def test_only_browser_renderable_images_are_served_inline(app):
    """HEIC, TIFF and raw are downloadable but cannot be shown, so they must not
    reach the inline route."""
    allowed = app.config['ALLOWED_EXTENSIONS']
    inline = app.config['IMAGE_EXTENSIONS']
    assert inline <= allowed
    for ext in ('heic', 'heif', 'tif', 'tiff', 'dng', 'cr2', 'nef', 'arw', 'raw'):
        assert ext in allowed and ext not in inline
    assert 'svg' not in inline and 'svg' not in allowed


def test_video_is_viewable_but_is_still_not_an_image(app, signed_in, wo):
    """Video is served inline so it can be watched in place — but it is not an
    image, so it gets no thumbnail and cannot be an asset photo."""
    upload(signed_in, wo.id, 'clip.mp4')
    att = Attachment.query.filter_by(original_filename='clip.mp4').one()
    assert signed_in.get(f'/attachments/{att.id}/inline').status_code == 200
    assert signed_in.get(f'/attachments/{att.id}/download').status_code == 200
    assert att.is_viewable and not att.is_image
    assert 'mp4' not in app.config['IMAGE_EXTENSIONS']


# ── the size limit ─────────────────────────────────────────────────────────

def test_the_default_limit_is_100mb(app):
    """Raised from 50 MB: a phone clip of a fault is easily tens of megabytes."""
    from config import Config
    assert Config.MAX_UPLOAD_MB == 100
    assert Config.MAX_CONTENT_LENGTH == 100 * 1024 * 1024


def test_the_limit_is_still_enforced(signed_in, wo, app):
    app.config['MAX_CONTENT_LENGTH'] = 2048
    response = upload(signed_in, wo.id, 'huge.mp4', content=b'x' * 50_000)
    assert b'too large' in response.data
    assert Attachment.query.count() == 0
