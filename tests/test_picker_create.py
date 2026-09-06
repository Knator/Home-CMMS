"""Creating a record from the "+" beside the picker that needs it.

The button is a real link to the real create page, upgraded by JS into a modal
that frames that same page with ?embedded=1. Two things have to hold: the
embedded page must drop the surrounding chrome but keep the whole form, and a
successful create must hand the record back instead of navigating — the page
underneath is usually a half-filled work order.
"""
import re

import pytest

from tests.conftest import CSRF, prime_csrf


@pytest.fixture
def signed_in(client, db, user, login):
    login()
    prime_csrf(client)
    return client


# (url, how many pickers that form has)
FORMS = [
    ('/work-orders/new', 3),   # asset, location, job plan
    ('/pms/new', 3),           # asset, location, job plan
    ('/assets/new', 2),        # location, parent asset
    ('/locations/new', 1),     # parent location
]


@pytest.mark.parametrize('url,expected', FORMS)
def test_every_picker_offers_a_create_button(signed_in, url, expected):
    html = signed_in.get(url).get_data(as_text=True)
    assert len(re.findall(r'data-create-modal', html)) == expected


@pytest.mark.parametrize('url,_expected', FORMS)
def test_the_button_works_without_javascript(signed_in, url, _expected):
    """It must stay a real link: JS only upgrades it in place."""
    html = signed_in.get(url).get_data(as_text=True)
    for anchor in re.findall(r'<a class="picker-add".*?</a>', html, re.S):
        assert 'href="/' in anchor          # a real destination, not href="#"
        assert 'target="_blank"' in anchor
        assert 'rel="noopener"' in anchor   # the opened tab cannot reach us
        assert 'aria-label=' in anchor


def test_each_button_names_the_select_it_fills(signed_in):
    html = signed_in.get('/work-orders/new').get_data(as_text=True)
    targets = re.findall(r'data-create-target="([^"]+)"', html)
    assert sorted(targets) == ['asset_id', 'job_plan_id', 'location_id']
    # Every named select actually exists on the page, or the option would be
    # inserted into nothing.
    for target in targets:
        assert f'id="{target}"' in html


# ── the embedded form ──────────────────────────────────────────────────────

@pytest.mark.parametrize('url', ['/assets/new', '/locations/new', '/job-plans/new'])
def test_embedded_drops_the_chrome_but_keeps_the_form(signed_in, url):
    full = signed_in.get(url).get_data(as_text=True)
    embedded = signed_in.get(f'{url}?embedded=1').get_data(as_text=True)

    assert 'class="sidebar"' in full
    assert 'class="sidebar"' not in embedded      # no nav inside the dialog
    assert 'topbar' not in embedded
    # but it is the same form, not a reduced copy
    assert '<form' in embedded and 'csrf_token' in embedded


def test_embedded_create_hands_the_record_back_instead_of_redirecting(signed_in):
    response = signed_in.post('/assets/new?embedded=1', data={
        'csrf_token': CSRF, 'name': 'Compressor', 'status': 'active',
    })
    assert response.status_code == 200          # not a 302
    body = response.get_data(as_text=True)
    assert 'postMessage' in body
    assert '"kind": "asset"' in body
    assert 'Compressor (AST-00001)' in body


def test_the_message_is_not_broadcast_to_any_origin(signed_in):
    """A wildcard targetOrigin would hand the new record to any page that
    framed us."""
    body = signed_in.post('/assets/new?embedded=1', data={
        'csrf_token': CSRF, 'name': 'Widget', 'status': 'active',
    }).get_data(as_text=True)
    assert "window.location.origin" in body
    assert "'*'" not in body and '"*"' not in body


def test_a_new_asset_reports_its_location_so_the_parent_can_inherit_it(
        signed_in, db):
    from app.services import create_location
    room = create_location(name='Utility Room')
    body = signed_in.post('/assets/new?embedded=1', data={
        'csrf_token': CSRF, 'name': 'Boiler', 'status': 'active',
        'location_id': str(room.id),
    }).get_data(as_text=True)
    assert f'"location_id": {room.id}' in body


@pytest.mark.parametrize('url,kind', [
    ('/assets/new', 'asset'),
    ('/locations/new', 'location'),
])
def test_normal_create_still_redirects(signed_in, url, kind):
    """The embedded path must not change what the ordinary form does."""
    response = signed_in.post(url, data={
        'csrf_token': CSRF, 'name': f'Ordinary {kind}', 'status': 'active',
    })
    assert response.status_code == 302
    assert 'postMessage' not in response.get_data(as_text=True)


def test_embedded_validation_errors_re_render_the_embedded_form(signed_in):
    """A rejected submission must stay in the dialog, not bounce to a full page
    with a sidebar inside the iframe."""
    response = signed_in.post('/assets/new?embedded=1', data={
        'csrf_token': CSRF, 'name': '', 'status': 'active',
    })
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'class="sidebar"' not in body
    assert '<form' in body
