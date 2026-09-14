"""The application icons.

The icons are committed artwork — there is no generator in the repository, so
these check the files themselves. What they are really protecting is the two
decisions that are invisible from the markup: that the .ico carries all three
sizes, and that its 16px frame is a simplified drawing rather than a shrink.
"""
import pathlib

import pytest

from tests.conftest import make_user

ROOT = pathlib.Path(__file__).resolve().parent.parent
ICONS = ROOT / 'app' / 'static' / 'icons'


@pytest.fixture
def admin_client(client, db, login):
    make_user('admin', role='admin', password='Password123!')
    login('admin', 'Password123!')
    return client


def test_every_referenced_icon_exists():
    for name in ('favicon.svg', 'favicon.ico', 'apple-touch-icon.png', 'mark.svg'):
        assert (ICONS / name).is_file(), name


def test_the_page_links_them(admin_client):
    html = admin_client.get('/').get_data(as_text=True)
    assert 'icons/favicon.svg' in html
    assert 'icons/favicon.ico' in html
    assert 'icons/apple-touch-icon.png' in html
    assert 'icons/mark.svg' in html


def test_the_ico_carries_every_size():
    """A .ico can legitimately hold one frame, and a single 16px one looks right
    in a browser tab while being wrong everywhere else — bookmarks, the desktop,
    a pinned shortcut. So the sizes are asserted rather than assumed."""
    Image = pytest.importorskip('PIL.Image', reason='Pillow reads the .ico')
    with Image.open(ICONS / 'favicon.ico') as ico:
        assert sorted(ico.ico.sizes()) == [(16, 16), (32, 32), (48, 48)]


def test_the_small_frame_is_not_just_a_shrink():
    """16px is drawn with blunter teeth and a heavier roof on purpose: a house
    and a gear together are about twice the detail that survives a 16px box, and
    scaling the full mark down turns it to grey mush.

    Compared against a downscale of the 48px frame, which is what the file would
    contain if someone regenerated it the easy way.
    """
    Image = pytest.importorskip('PIL.Image', reason='Pillow reads the .ico')

    with Image.open(ICONS / 'favicon.ico') as ico:
        ico.size = (16, 16)
        small = ico.convert('RGBA')
    with Image.open(ICONS / 'favicon.ico') as ico:
        ico.size = (48, 48)
        shrunk = ico.convert('RGBA').resize((16, 16), Image.LANCZOS)

    differing = sum(a != b for a, b in zip(small.get_flattened_data(),
                                           shrunk.get_flattened_data()))
    assert differing > 20, (
        f'the 16px frame differs from a plain shrink of the 48px one in only '
        f'{differing} of 256 pixels — the deliberate simplification looks lost'
    )


def test_the_in_app_mark_is_not_currentcolor():
    """It is loaded through an <img>, which renders in an isolated document, so
    `currentColor` resolves to black rather than the page's colour and the mark
    disappears into the dark sidebar."""
    mark = (ICONS / 'mark.svg').read_text()
    assert 'currentColor' not in mark
    assert '#ffffff' in mark


def test_the_mark_has_no_tile_behind_it():
    """The sidebar version is the shape alone; a background rect would put a
    blue box on the sidebar."""
    assert '<rect' not in (ICONS / 'mark.svg').read_text()
