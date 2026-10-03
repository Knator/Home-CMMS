"""Pickers tell apart records that share a name.

Eight pickers formatted their own labels, so a location showed its path in one
form and only its name in another — and in a large house "Kitchen" can exist
nineteen times, distinguishable only by a number. Assets showed where they were
but never what they were part of.
"""
import pathlib
import re
from html.parser import HTMLParser

import pytest

from app.extensions import db as _db
from app.models.job_plan import JobPlan
from app.services import create_asset, create_location
from tests.conftest import make_user

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def signed_in(client, db, login):
    make_user('tester', role='admin', password='Password123!')
    login('tester', 'Password123!')
    return client


@pytest.fixture
def house(db):
    """Two Kitchens in different places, and a sub-assembly."""
    main = create_location(name='Main House')
    annexe = create_location(name='Guest Annexe')
    main_floor = create_location(name='Ground Floor', parent_id=main.id)
    annexe_floor = create_location(name='First Floor', parent_id=annexe.id)
    k1 = create_location(name='Kitchen', parent_id=main_floor.id)
    k2 = create_location(name='Kitchen', parent_id=annexe_floor.id)
    utility = create_location(name='Utility Room', parent_id=main_floor.id)
    furnace = create_asset(name='Furnace', location_id=utility.id)
    blower = create_asset(name='Blower Motor', location_id=utility.id,
                          parent_id=furnace.id)
    plan = JobPlan(name='Replace filters', description='Quarterly filter swap.')
    _db.session.add(plan)
    _db.session.commit()
    return dict(k1=k1, k2=k2, furnace=furnace, blower=blower, plan=plan,
                utility=utility)


class Options(HTMLParser):
    """Every <option> on a page, with its attributes, text and select name."""

    def __init__(self):
        super().__init__()
        self.found, self._current, self._select = [], None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'select':
            self._select = attrs.get('name')
        elif tag == 'option':
            self._current = dict(attrs, select=self._select, text='')

    def handle_data(self, data):
        if self._current is not None:
            self._current['text'] += data

    def handle_endtag(self, tag):
        if tag == 'option' and self._current is not None:
            self._current['text'] = ' '.join(self._current['text'].split())
            self.found.append(self._current)
            self._current = None


def options(client, url, select):
    parser = Options()
    parser.feed(client.get(url).get_data(as_text=True))
    return {o['value']: o for o in parser.found if o['select'] == select and o['value']}


# ── what each kind of option says ──────────────────────────────────────────

def test_two_locations_with_one_name_are_told_apart(signed_in, house):
    opts = options(signed_in, '/work-orders/new', 'location_id')
    first = opts[str(house['k1'].id)]
    second = opts[str(house['k2'].id)]

    assert first['data-primary'] == second['data-primary'] == 'Kitchen'
    assert first['data-context'] == 'Main House › Ground Floor'
    assert second['data-context'] == 'Guest Annexe › First Floor'


def test_a_location_context_is_its_ancestors_not_itself(signed_in, house):
    """The name is already on the top line; repeating it in the path is noise."""
    opts = options(signed_in, '/work-orders/new', 'location_id')
    assert 'Kitchen' not in opts[str(house['k1'].id)]['data-context']


def test_a_top_level_location_has_no_context(signed_in, house):
    opts = options(signed_in, '/work-orders/new', 'location_id')
    top = next(o for o in opts.values() if o['data-primary'] == 'Main House')
    assert top['data-context'] == ''
    assert top['text'] == f"Main House ({top['data-code']})", 'stray separator'


def test_a_sub_assembly_says_what_it_is_part_of(signed_in, house):
    """What the old label never said: it showed the room, not the parent."""
    opts = options(signed_in, '/work-orders/new', 'asset_id')
    blower = opts[str(house['blower'].id)]
    furnace = house['furnace']
    assert blower['data-context'].startswith(
        f'part of Furnace ({furnace.asset_number})'), blower['data-context']
    assert 'Utility Room' in blower['data-context']


def test_a_top_level_asset_shows_only_where_it_is(signed_in, house):
    opts = options(signed_in, '/work-orders/new', 'asset_id')
    furnace = opts[str(house['furnace'].id)]
    assert furnace['data-context'] == 'Main House › Ground Floor › Utility Room'


def test_a_job_plan_shows_its_description(signed_in, house):
    opts = options(signed_in, '/work-orders/new', 'job_plan_id')
    plan = opts[str(house['plan'].id)]
    assert plan['data-primary'] == 'Replace filters'
    assert plan['data-context'] == 'Quarterly filter swap.'
    assert 'data-code' not in plan, 'job plans have no number to show'


def test_without_javascript_the_text_still_tells_them_apart(signed_in, house):
    """A native select shows the option text and nothing else. It is long, but
    it is unambiguous, which is the whole point."""
    opts = options(signed_in, '/work-orders/new', 'location_id')
    texts = {opts[str(house[k].id)]['text'] for k in ('k1', 'k2')}
    assert len(texts) == 2, texts
    assert all('›' in t for t in texts), texts


def test_names_are_escaped_in_the_data_attributes(signed_in, db):
    loc = create_location(name='Shed "B" <rear>')
    raw = signed_in.get('/work-orders/new').get_data(as_text=True)
    assert 'data-primary="Shed &#34;B&#34; &lt;rear&gt;"' in raw \
        or 'data-primary="Shed &quot;B&quot; &lt;rear&gt;"' in raw
    assert '<rear>' not in raw


# ── one definition, every picker ───────────────────────────────────────────

PICKERS = [
    ('/work-orders/new', 'asset_id'), ('/work-orders/new', 'location_id'),
    ('/work-orders/new', 'job_plan_id'),
    ('/pms/new', 'asset_id'), ('/pms/new', 'location_id'), ('/pms/new', 'job_plan_id'),
    ('/assets/new', 'location_id'), ('/assets/new', 'parent_id'),
    ('/locations/new', 'parent_id'),
    ('/assets/', 'location_id'),
]


@pytest.mark.parametrize('url,select', PICKERS)
def test_every_picker_carries_the_structured_label(signed_in, house, url, select):
    """Eight pickers drifted apart once already, each formatting its own label."""
    opts = options(signed_in, url, select)
    assert opts, f'{url} {select}: no options'
    assert all('data-primary' in o for o in opts.values()), (url, select)


def test_no_template_hand_writes_a_record_label():
    """A new picker should reach for the macros in _pickers.html. This finds an
    <option> that formats an asset or location number itself."""
    offenders = []
    for path in (ROOT / 'app' / 'templates').rglob('*.html'):
        if path.name == '_pickers.html':
            continue
        for line in path.read_text().splitlines():
            if '<option' in line and re.search(r'\.(asset_number|location_number)', line):
                offenders.append(f'{path.relative_to(ROOT)}: {line.strip()[:80]}')
    assert not offenders, offenders


# ── cost ───────────────────────────────────────────────────────────────────

def test_the_context_does_not_cost_a_query_per_option(signed_in, db):
    """Every location's ancestors and every asset's parent are walked to build
    the labels. They resolve from the session's identity map because the
    pickers have already loaded the rows — a query each would be hundreds."""
    from sqlalchemy import event

    floors = []
    for i in range(6):
        area = create_location(name=f'Area {i}')
        floors.append(create_location(name='Floor', parent_id=area.id))
    rooms = [create_location(name=f'Room {i}', parent_id=floors[i % 6].id)
             for i in range(30)]
    parents = [create_asset(name=f'Unit {i}', location_id=rooms[i].id) for i in range(20)]
    for i in range(20):
        create_asset(name=f'Part {i}', location_id=rooms[i].id, parent_id=parents[i].id)

    seen = []
    engine = _db.engines[None]
    listener = lambda *a: seen.append(1)
    event.listen(engine, 'before_cursor_execute', listener)
    try:
        signed_in.get('/work-orders/new')
    finally:
        event.remove(engine, 'before_cursor_execute', listener)
    assert len(seen) < 25, f'{len(seen)} queries for ~80 options'
