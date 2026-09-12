"""The shared search box on the location, asset and PM lists.

Same control and same engine as the work order and job plan lists; what differs
is which columns each list looks at, and that two of them are hierarchies.
"""
from datetime import date

import pytest

from app.extensions import db as _db
from app.models.pm import PM
from app.services import create_asset, create_location
from tests.conftest import prime_csrf


@pytest.fixture
def signed_in(client, db, user, login):
    login()
    prime_csrf(client)
    return client


@pytest.fixture
def data(db):
    basement = create_location(name='Basement', description='Below stairs',
                               notes='Damp in winter')
    utility = create_location(name='Utility Room', parent_id=basement.id)
    create_location(name='Attic', notes='Insulation replaced 2024')

    furnace = create_asset(name='Furnace', location_id=utility.id,
                           notes='Model FX-9')
    create_asset(name='Compressor', parent_id=furnace.id, notes='Inside the unit')
    create_asset(name='Sump Pump', location_id=basement.id)

    _db.session.add(PM(name='Filter Change', interval_days=90,
                       next_due_date=date.today(), notes='Use 16x25x1'))
    _db.session.add(PM(name='Gutter Clean', interval_days=180,
                       next_due_date=date.today()))
    _db.session.commit()


def found(client, path, query, candidates):
    html = client.get(path + query).get_data(as_text=True)
    return {name for name in candidates if name in html}


LOCATIONS = {'Basement', 'Utility Room', 'Attic'}
ASSETS = {'Furnace', 'Compressor', 'Sump Pump'}
PMS = {'Filter Change', 'Gutter Clean'}


# ── locations: name, description, notes ────────────────────────────────────

@pytest.mark.parametrize('needle,expected', [
    ('attic', {'Attic'}),
    ('below', {'Basement'}),            # description
    ('insulation', {'Attic'}),          # notes
    ('ATTIC', {'Attic'}),               # case-insensitive
])
def test_location_search(signed_in, data, needle, expected):
    assert found(signed_in, '/locations/', f'?q={needle}', LOCATIONS) == expected


# ── assets: name, notes ────────────────────────────────────────────────────

@pytest.mark.parametrize('needle,expected', [
    ('sump', {'Sump Pump'}),
    ('FX-9', {'Furnace'}),              # notes
    ('SUMP', {'Sump Pump'}),
])
def test_asset_search(signed_in, data, needle, expected):
    assert found(signed_in, '/assets/', f'?q={needle}', ASSETS) == expected


# ── PMs: name, notes ───────────────────────────────────────────────────────

@pytest.mark.parametrize('needle,expected', [
    ('gutter', {'Gutter Clean'}),
    ('16x25', {'Filter Change'}),       # notes
])
def test_pm_search(signed_in, data, needle, expected):
    assert found(signed_in, '/pms/', f'?q={needle}', PMS) == expected


# ── the hierarchies ────────────────────────────────────────────────────────

def test_a_matching_location_shows_even_when_its_parent_does_not(signed_in, data):
    """Filtered first, arranged second: hierarchy_ordered promotes a match whose
    parent was filtered away, so a child is still findable on its own."""
    assert found(signed_in, '/locations/', '?q=utility', LOCATIONS) == {'Utility Room'}


def test_a_matching_sub_assembly_shows_without_its_parent(signed_in, data):
    assert found(signed_in, '/assets/', '?q=compressor', ASSETS) == {'Compressor'}


# ── shared behaviour ───────────────────────────────────────────────────────

@pytest.mark.parametrize('path,candidates', [
    ('/locations/', LOCATIONS), ('/assets/', ASSETS), ('/pms/', PMS)])
def test_an_empty_search_lists_everything(signed_in, data, path, candidates):
    assert found(signed_in, path, '', candidates) == candidates


@pytest.mark.parametrize('path,candidates', [
    ('/locations/', LOCATIONS), ('/assets/', ASSETS), ('/pms/', PMS)])
def test_no_hits_lists_nothing(signed_in, data, path, candidates):
    assert found(signed_in, path, '?q=zzzznotfound', candidates) == set()


@pytest.mark.parametrize('path', ['/locations/', '/assets/', '/pms/'])
def test_every_list_offers_the_same_box(signed_in, data, path):
    """One macro, so the five lists cannot drift apart."""
    html = signed_in.get(path).get_data(as_text=True)
    for markup in ('class="filter-search"', 'class="regex-check"',
                   'for="regex-toggle"'):
        assert markup in html, f'{path}: {markup}'


@pytest.mark.parametrize('path,needle,expected', [
    ('/locations/', 'attic%7Cbasement', {'Attic', 'Basement'}),
    ('/assets/', 'sump%7Cfurnace', {'Sump Pump', 'Furnace'}),
    ('/pms/', 'filter%7Cgutter', {'Filter Change', 'Gutter Clean'}),
])
def test_the_regex_toggle_works_on_every_list(signed_in, data, path, needle,
                                              expected):
    candidates = {'/locations/': LOCATIONS, '/assets/': ASSETS, '/pms/': PMS}[path]
    assert found(signed_in, path, f'?regex=1&q={needle}', candidates) == expected


@pytest.mark.parametrize('path', ['/locations/', '/assets/', '/pms/'])
def test_an_invalid_pattern_is_reported_on_every_list(signed_in, data, path):
    response = signed_in.get(path + '?regex=1&q=%5B')
    assert response.status_code == 200
    assert b'not a valid regular expression' in response.data


@pytest.mark.parametrize('path', ['/locations/', '/assets/', '/pms/'])
def test_search_combines_with_the_existing_filters(signed_in, data, path):
    """Each of these lists already had a show=active/all toggle."""
    response = signed_in.get(path + '?show=all&q=zzzznotfound')
    assert response.status_code == 200


def test_asset_search_still_respects_the_location_filter(signed_in, data):
    from app.models.location import Location
    basement = Location.query.filter_by(name='Basement').one()
    # Sump Pump is in the basement; Furnace is not.
    assert found(signed_in, '/assets/',
                 f'?location_id={basement.id}&q=pump', ASSETS) == {'Sump Pump'}
    assert found(signed_in, '/assets/',
                 f'?location_id={basement.id}&q=FX-9', ASSETS) == set()
