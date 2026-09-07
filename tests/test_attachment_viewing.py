"""Clicking an attachment's name shows it; a separate button saves it.

The inline route is what "show" means, so what it will and will not serve is
the security-relevant half of this: inline is the disposition where the browser
renders content rather than filing it away.
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
    return create_work_order(title='Pump noise', wo_type='unplanned')


def attach(client, wo_id, filename, content=b'data'):
    client.post(f'/work-orders/{wo_id}/attachments', data={
        'csrf_token': CSRF, 'file': (io.BytesIO(content), filename),
    }, content_type='multipart/form-data', follow_redirects=True)
    return Attachment.query.filter_by(original_filename=filename).one()


# ── what may be viewed ─────────────────────────────────────────────────────

@pytest.mark.parametrize('filename', [
    'manual.pdf', 'fault.mp4', 'noise.mp3', 'notes.txt', 'photo.png',
])
def test_viewable_files_are_served_inline(signed_in, wo, filename):
    att = attach(signed_in, wo.id, filename)
    response = signed_in.get(f'/attachments/{att.id}/inline')
    assert response.status_code == 200
    assert 'attachment' not in response.headers.get('Content-Disposition', '')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.parametrize('filename', ['drawing.dwg', 'logs.zip', 'model.stl',
                                      'sheet.xlsx', 'photo.heic'])
def test_files_the_browser_cannot_render_are_not_served_inline(signed_in, wo, filename):
    """Nothing to show, so the name downloads instead and inline refuses."""
    att = attach(signed_in, wo.id, filename)
    assert signed_in.get(f'/attachments/{att.id}/inline').status_code == 404
    assert signed_in.get(f'/attachments/{att.id}/download').status_code == 200


@pytest.mark.parametrize('filename,content', [
    ('report.xml', b'<?xml version="1.0"?><x>hi</x>'),
    ('data.json', b'{"a": 1}'),
    ('notes.md', b'# heading'),
    ('export.csv', b'a,b\n1,2'),
])
def test_text_formats_are_forced_to_plain_text(signed_in, wo, filename, content):
    """Served as its own type, XML can carry a stylesheet that runs script in
    this origin. As text/plain it cannot."""
    att = attach(signed_in, wo.id, filename, content)
    response = signed_in.get(f'/attachments/{att.id}/inline')
    assert response.status_code == 200
    assert response.headers['Content-Type'].startswith('text/plain')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_inline_still_requires_a_login(client, db, user, login, wo):
    login()
    prime_csrf(client)
    att = attach(client, wo.id, 'manual.pdf')
    client.get('/auth/logout')
    assert client.get(f'/attachments/{att.id}/inline').status_code in (302, 401)


# ── the list UI ────────────────────────────────────────────────────────────

def detail(client, wo):
    return client.get(f'/work-orders/{wo.id}').get_data(as_text=True)


def test_the_name_of_a_viewable_file_opens_a_view_not_a_download(signed_in, wo):
    att = attach(signed_in, wo.id, 'manual.pdf')
    html = detail(signed_in, wo)
    assert f'class="attachment-name" href="/attachments/{att.id}/inline"' in html


def test_an_image_name_opens_the_lightbox_like_its_thumbnail(signed_in, wo):
    att = attach(signed_in, wo.id, 'photo.png')
    html = detail(signed_in, wo)
    # two lightbox anchors for the one file: the thumbnail and now the name
    assert html.count(f'/attachments/{att.id}/inline') >= 2
    assert 'data-lightbox' in html


def test_an_unviewable_name_still_downloads(signed_in, wo):
    att = attach(signed_in, wo.id, 'drawing.dwg')
    html = detail(signed_in, wo)
    assert f'class="attachment-name" href="/attachments/{att.id}/download"' in html
    assert 'cannot be shown in the browser' in html


def test_every_attachment_gets_a_download_button(signed_in, wo):
    viewable = attach(signed_in, wo.id, 'manual.pdf')
    other = attach(signed_in, wo.id, 'drawing.dwg')
    html = detail(signed_in, wo)
    for att in (viewable, other):
        assert (f'<a class="btn btn-ghost btn-sm" download\n'
                f'     href="/attachments/{att.id}/download"') in html or \
               f'href="/attachments/{att.id}/download"' in html
    assert html.count('>Download</a>') >= 2


def test_the_download_button_sits_with_rename_and_delete(signed_in, wo):
    att = attach(signed_in, wo.id, 'manual.pdf')
    html = detail(signed_in, wo)
    download_at = html.index(f'/attachments/{att.id}/download')
    rename_at = html.index(f'/attachments/{att.id}/rename')
    assert download_at < rename_at        # Download, then Rename, then Delete


# ── the preview slot is a link too ─────────────────────────────────────────
#
# A video or a PDF has no thumbnail, but the chip standing in for one should
# still open the file — that box is where people click.

@pytest.mark.parametrize('filename', ['fault.mp4', 'manual.pdf', 'noise.mp3',
                                      'notes.txt'])
def test_the_extension_chip_opens_viewable_files(signed_in, wo, filename):
    att = attach(signed_in, wo.id, filename)
    html = detail(signed_in, wo)
    assert (f'<a class="attachment-icon" href="/attachments/{att.id}/inline"'
            in html)


def test_the_chip_for_an_undisplayable_file_downloads_instead(signed_in, wo):
    att = attach(signed_in, wo.id, 'layout.dwg')
    html = detail(signed_in, wo)
    assert (f'<a class="attachment-icon" href="/attachments/{att.id}/download"'
            in html)
    assert 'cannot be shown in the browser' in html


def test_the_chip_is_labelled_for_screen_readers(signed_in, wo):
    """It was a decorative span with aria-hidden; as a link it needs a name."""
    attach(signed_in, wo.id, 'fault.mp4')
    html = detail(signed_in, wo)
    assert 'aria-label="View fault.mp4"' in html


def test_the_chip_still_shows_the_extension(signed_in, wo):
    attach(signed_in, wo.id, 'fault.mp4')
    assert '>MP4</a>' in detail(signed_in, wo)


def test_the_thumbnail_fallback_no_longer_builds_markup_from_a_filename(
        signed_in, wo):
    """The old onerror interpolated the extension into an HTML string. The
    extension comes from a name the uploader chose, so the chip is now rendered
    up front and merely revealed."""
    attach(signed_in, wo.id, 'photo.png')
    html = detail(signed_in, wo)
    assert 'outerHTML' not in html
    assert "this.hidden = true; this.nextElementSibling.hidden = false;" in html


def test_a_quoted_filename_is_defused_before_it_is_ever_stored(signed_in, wo):
    """Two independent reasons the extension chip cannot carry script.

    secure_filename() strips the quotes at upload, so original_filename never
    holds them — the old string-building onerror was not exploitable through
    this route. The rewritten handler interpolates nothing at all, so it no
    longer depends on that.
    """
    signed_in.post(f'/work-orders/{wo.id}/attachments', data={
        'csrf_token': CSRF,
        'file': (io.BytesIO(b'x'), "evil'onerror='alert(1).png"),
    }, content_type='multipart/form-data', follow_redirects=True)

    stored = Attachment.query.one().original_filename
    assert "'" not in stored
    assert stored == 'evilonerroralert1.png'

    html = detail(signed_in, wo)
    assert "onerror='alert(1)" not in html
