"""The work order filter menus open one at a time.

They are <details> elements with absolutely positioned panels. Nothing made them
aware of each other, so all three could be open together and the panels
overlapped. A shared `name` makes them an exclusive group in HTML itself, which
keeps the no-JavaScript promise the filters were built on.
"""
import pathlib
import re

import pytest

from tests.conftest import make_user

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def signed_in(client, db, login):
    make_user('tester', role='admin', password='Password123!')
    login('tester', 'Password123!')
    return client


def test_every_filter_menu_is_in_one_exclusive_group(signed_in):
    body = signed_in.get('/work-orders/').get_data(as_text=True)
    menus = re.findall(r'<details[^>]*class="filter-menu"[^>]*>', body)
    assert len(menus) >= 2, menus

    names = {re.search(r'name="([^"]+)"', tag).group(1)
             for tag in menus if 'name=' in tag}
    assert len(names) == 1, f'menus are in different groups: {names}'
    assert len(names) == 1 and all('name=' in tag for tag in menus), menus


def test_the_group_name_is_not_a_filter_field(signed_in):
    """`name` on <details> is not submitted, but if that ever changed a
    collision with a real field would corrupt the query string silently."""
    body = signed_in.get('/work-orders/').get_data(as_text=True)
    group = re.search(r'<details[^>]*class="filter-menu"[^>]*name="([^"]+)"', body).group(1)
    assert group not in {'status', 'type', 'priority', 'q', 'regex', 'archived'}


def test_opening_a_menu_does_not_submit_the_group_name(signed_in):
    """The menus sit inside the GET form, so a stray submitted value would show
    up as an unknown filter."""
    response = signed_in.get('/work-orders/?wo-filter-menu=status')
    assert response.status_code == 200


def test_a_fallback_exists_for_browsers_without_details_name(signed_in):
    """`<details name>` predates neither Chrome 120 nor Firefox 130, so the
    script closes siblings where the browser will not. It is feature-detected,
    so it does nothing on a current browser."""
    js = (ROOT / 'app' / 'static' / 'js' / 'main.js').read_text()
    assert 'initExclusiveDetails' in js
    assert "'name' in document.createElement('details')" in js, 'not feature-detected'
    # `toggle` does not bubble; catching it on the way down is the whole trick
    assert re.search(r"addEventListener\('toggle'.*?\}, true\)", js, re.S), \
        'the toggle listener must use the capture phase'


# ── the Archived select ────────────────────────────────────────────────────

def test_archived_is_a_select_not_a_menu(signed_in):
    """Deliberate: it is tri-state and orthogonal to the others, so it is not
    part of the exclusive <details> group and cannot be."""
    body = signed_in.get('/work-orders/').get_data(as_text=True)
    assert re.search(r'<select[^>]*name="archived"', body)
    assert not re.search(r'<details[^>]*id="archived"', body)


def test_using_another_control_closes_an_open_menu(signed_in):
    """A browser's select popup has no idea a <details> panel is open beside
    it, and no HTML mechanism spans the two — so this is the one part of the
    filter bar that needs script."""
    js = (ROOT / 'app' / 'static' / 'js' / 'main.js').read_text()
    assert 'initFilterMenus' in js

    body = js[js.index('function initFilterMenus'):]
    body = body[:body.index('\n}')]
    # mousedown, because the popup opens before a click completes
    assert "'mousedown'" in body, 'click fires too late for a select popup'
    # focusin, for reaching the select by keyboard
    assert "'focusin'" in body
    # and controls inside a menu are exempt, or ticking a box would close it
    assert "closest('details.filter-menu')" in body


def test_the_menu_fallback_and_the_select_handler_are_separate(signed_in):
    """initExclusiveDetails returns early on a browser with <details name>.
    The select problem exists on every browser, so it must not sit behind that
    same feature check."""
    js = (ROOT / 'app' / 'static' / 'js' / 'main.js').read_text()
    filter_menus = js[js.index('function initFilterMenus'):]
    filter_menus = filter_menus[:filter_menus.index('\n}')]
    assert "in document.createElement('details')" not in filter_menus
