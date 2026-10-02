"""Filters survive, and the arrows step, on every list that has a detail page.

Work orders are covered separately in test_work_order_stepping.py; this is the
other four, plus the properties that must hold identically across all five —
which is the reason they share app/navigation.py and _stepper.html rather than
having five copies that drift.
"""
import pytest

from app.extensions import db as _db
from app.models.job_plan import JobPlan
from app.models.pm import PM
from app.services import create_asset, create_location
from tests.conftest import make_user

from datetime import date


@pytest.fixture
def signed_in(client, db, login):
    make_user('tester', role='admin', password='Password123!')
    login('tester', 'Password123!')
    return client


@pytest.fixture
def records(db):
    """Three of each, so there is a first, a middle and a last."""
    out = {}
    # Every name carries an 'a', so `q=a` narrows nothing away and the tests
    # exercise the filter being *carried*, not the filter excluding records.
    out['locations'] = [create_location(name=n) for n in ('Attic', 'Basement', 'Garage')]
    out['assets'] = [create_asset(name=n, location_id=out['locations'][0].id)
                     for n in ('Aboiler', 'Bchiller', 'Cdryera')]
    plans = [JobPlan(name=n) for n in ('Aanneal', 'Balance', 'Calibrate')]
    pms = [PM(name=n, interval_days=30, next_due_date=date.today(), is_active=True)
           for n in ('Alpha PM', 'Beta PM', 'Gamma PM')]
    _db.session.add_all(plans + pms)
    _db.session.commit()
    out['job_plans'] = plans
    out['pms'] = pms
    return out


# (module, url prefix, ordered-as-displayed key)
MODULES = [
    ('pms', '/pms'),
    ('job_plans', '/job-plans'),
    ('assets', '/assets'),
    ('locations', '/locations'),
]


def detail_links(body, prefix):
    """Every link to a detail page under `prefix`, in the order they appear.

    The path is split off first: a row link carries the filters, so the href
    ends `/3?show=all` rather than `/3`.
    """
    seen, out = set(), []
    for chunk in body.split('href="')[1:]:
        href = chunk.split('"')[0]
        path = href.split('?', 1)[0].rstrip('/')
        if not path.startswith(f'{prefix}/'):
            continue
        tail = path.rsplit('/', 1)[-1]
        if tail.isdigit() and int(tail) not in seen:
            seen.add(int(tail))
            out.append((int(tail), href))
    return out


def body_of(client, url):
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code)
    return response.get_data(as_text=True)


@pytest.mark.parametrize('module,prefix', MODULES)
def test_row_links_carry_the_filters(signed_in, records, module, prefix):
    body = body_of(signed_in, f'{prefix}/?q=a&show=all')
    links = detail_links(body, prefix)
    assert links, f'no detail links on {prefix}'
    assert all('q=a' in href for _id, href in links), links[:3]


@pytest.mark.parametrize('module,prefix', MODULES)
def test_back_returns_to_the_filtered_list(signed_in, records, module, prefix):
    first = records[module][0]
    body = body_of(signed_in, f'{prefix}/{first.id}?show=all&q=a')
    back = [h.split('"')[0] for h in body.split('class="btn btn-secondary page-back"')[0:1]]
    assert 'page-back' in body, f'no Back control on {prefix} detail'
    # the href immediately preceding the page-back class
    chunk = body[:body.index('page-back')]
    href = chunk[chunk.rindex('href="') + 6:].split('"')[0]
    assert 'show=all' in href and 'q=a' in href, href


@pytest.mark.parametrize('module,prefix', MODULES)
def test_a_middle_record_steps_both_ways(signed_in, records, module, prefix):
    body = body_of(signed_in, f'{prefix}/?show=all')
    ids_in_order = [i for i, _href in detail_links(body, prefix)]
    assert len(ids_in_order) >= 3, ids_in_order

    middle = ids_in_order[1]
    body = body_of(signed_in, f'{prefix}/{middle}?show=all')
    assert '2 of' in body, f'{prefix} middle record is not second'
    assert f'{prefix}/{ids_in_order[0]}' in body
    assert f'{prefix}/{ids_in_order[2]}' in body


@pytest.mark.parametrize('module,prefix', MODULES)
def test_the_ends_are_inert(signed_in, records, module, prefix):
    body = body_of(signed_in, f'{prefix}/?show=all')
    ids_in_order = [i for i, _href in detail_links(body, prefix)]

    first = body_of(signed_in, f'{prefix}/{ids_in_order[0]}?show=all')
    assert 'This is the first in the list' in first
    i = first.index('This is the first in the list')
    tag = first[first.rindex('<', 0, i):first.index('>', i) + 1]
    assert tag.startswith('<span') and 'href' not in tag, tag

    last = body_of(signed_in, f'{prefix}/{ids_in_order[-1]}?show=all')
    assert 'This is the last in the list' in last


@pytest.mark.parametrize('module,prefix', MODULES)
def test_no_arrows_for_a_single_record(signed_in, records, module, prefix):
    """A list of one has nowhere to step, so the control is absent rather than
    present-and-dead at both ends."""
    one = records[module][0]
    body = body_of(signed_in, f'{prefix}/{one.id}?q={one.name}')
    assert 'wo-steps' not in body, f'{prefix} shows arrows for a list of one'


# ── the hierarchical lists ─────────────────────────────────────────────────

def test_assets_step_in_the_order_the_tree_displays(signed_in, db):
    """Assets and locations are shown depth-first, a child indented under its
    parent, which is not the alphabetical order of the query behind it. The
    arrows have to follow what is on screen — stepping the raw query would jump
    around the page.

    `Zsub` sorts last by name but renders second, directly under its parent.
    """
    loc = create_location(name='Shed')
    parent = create_asset(name='Aparent', location_id=loc.id)
    child = create_asset(name='Zsub', location_id=loc.id, parent_id=parent.id)
    other = create_asset(name='Mother', location_id=loc.id)

    body = body_of(signed_in, '/assets/?show=all')
    order = [i for i, _href in detail_links(body, '/assets')]
    assert order == [parent.id, child.id, other.id], order

    # and the arrows agree with that, rather than with the alphabetical order
    on_child = body_of(signed_in, f'/assets/{child.id}?show=all')
    assert '2 of 3' in on_child
    assert f'/assets/{parent.id}' in on_child
    assert f'/assets/{other.id}' in on_child


def test_locations_step_in_the_order_the_tree_displays(signed_in, db):
    outer = create_location(name='Aouter')
    inner = create_location(name='Zinner', parent_id=outer.id)
    other = create_location(name='Mother')

    body = body_of(signed_in, '/locations/?show=all')
    order = [i for i, _href in detail_links(body, '/locations')]
    assert order == [outer.id, inner.id, other.id], order

    on_inner = body_of(signed_in, f'/locations/{inner.id}?show=all')
    assert '2 of 3' in on_inner
