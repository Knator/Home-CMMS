"""Lists are paged, and the pages behave under the filters.

Rendering every row cost 2.5s and 8MB of HTML at 10,000 work orders, and the
cost was the rendering rather than the query — so indexes do not help and a page
size does. These pin the parts that are easy to get subtly wrong: that filters
survive paging, that the page number does not leak onto row links, and that a
tree is never split between a parent and its child.
"""
import pathlib

import pytest

from app.extensions import db as _db
from app.navigation import PAGE_SIZE, Page, paginate_list, paginate_tree, page_number
from app.models.job_plan import JobPlan
from app.models.pm import PM
from app.services import create_asset, create_location, create_work_order
from tests.conftest import make_user

ROOT = pathlib.Path(__file__).resolve().parent.parent

from datetime import date


@pytest.fixture
def signed_in(client, db, login):
    make_user('tester', role='admin', password='Password123!')
    login('tester', 'Password123!')
    return client


@pytest.fixture
def records(db):
    """One of each, so every list has something to count."""
    from app.models.job_plan import JobPlan
    from app.models.pm import PM
    loc = create_location(name='Shed')
    create_asset(name='Mower', location_id=loc.id)
    _db.session.add(JobPlan(name='Service'))
    _db.session.add(PM(name='Service PM', interval_days=30, next_due_date=date.today()))
    _db.session.commit()
    create_work_order(title='Fix it', status='open')


@pytest.fixture
def many(db):
    """Enough to need a second page."""
    for i in range(PAGE_SIZE + 10):
        create_work_order(title=f'Job {i:03d} pump', status='open')
    return PAGE_SIZE + 10


def test_a_long_list_is_cut_into_pages(signed_in, many):
    body = signed_in.get('/work-orders/').get_data(as_text=True)
    assert body.count('class="row-link"') <= PAGE_SIZE * 2   # two links per row
    assert 'Page 1 of 2' in body
    assert f'{many} work orders' in body


def filter_bar(body):
    start = body.index('<form method="get" class="filters">')
    return body[start:body.index('</form>', start)]


def ids_on(client, url):
    body = client.get(url).get_data(as_text=True)
    found = []
    for chunk in body.split('href="')[1:]:
        href = chunk.split('"')[0]
        tail = href.split('?')[0].rstrip('/').split('/')[-1]
        if href.startswith('/work-orders/') and tail.isdigit() and int(tail) not in found:
            found.append(int(tail))
    return found


def test_the_second_page_holds_the_rest(signed_in, many):
    """Asserted on ids rather than titles: the list is newest-first, so which
    titles land on which page depends on creation order, and the property that
    matters is simply that the pages do not overlap and together hold everything.
    """
    first = ids_on(signed_in, '/work-orders/')
    second = ids_on(signed_in, '/work-orders/?page=2')

    assert 'Page 2 of 2' in signed_in.get('/work-orders/?page=2').get_data(as_text=True)
    assert len(first) == PAGE_SIZE
    assert len(second) == many - PAGE_SIZE
    assert not set(first) & set(second), 'the pages overlap'


def test_paging_keeps_the_filter(signed_in, many):
    create_work_order(title='Completed outlier', status='completed')
    body = signed_in.get('/work-orders/?status=open&page=2').get_data(as_text=True)
    assert 'Completed outlier' not in body
    # and the pager's own links carry it onward
    assert 'status=open' in body


def test_the_page_number_is_not_carried_onto_row_links(signed_in, many):
    """Otherwise every row on page 2 would link to a detail page that, on
    returning, pins you to page 2 of a different filter."""
    body = signed_in.get('/work-orders/?page=2&status=open').get_data(as_text=True)
    row_links = [chunk.split('"')[0] for chunk in body.split('href="')[1:]
                 if chunk.startswith('/work-orders/') and 'page=' in chunk.split('"')[0]]
    detail_links = [h for h in row_links if h.split('?')[0].rstrip('/').split('/')[-1].isdigit()]
    assert not detail_links, detail_links[:3]


def test_a_nonsense_page_number_is_not_an_error(signed_in, many):
    for bad in ('0', '-4', 'abc', ''):
        assert signed_in.get(f'/work-orders/?page={bad}').status_code == 200
    assert page_number({'page': 'abc'}) == 1


def test_stepping_crosses_page_boundaries(signed_in, many):
    """The arrows follow the filtered sequence, not the page. Stopping at the
    bottom of a page would make the last row on each page a dead end."""
    body = signed_in.get('/work-orders/').get_data(as_text=True)
    ids = []
    for chunk in body.split('href="')[1:]:
        href = chunk.split('"')[0]
        tail = href.split('?')[0].rstrip('/').split('/')[-1]
        if href.startswith('/work-orders/') and tail.isdigit() and int(tail) not in ids:
            ids.append(int(tail))

    last_on_page = ids[-1]
    detail = signed_in.get(f'/work-orders/{last_on_page}').get_data(as_text=True)
    assert f'{PAGE_SIZE} of {many}' in detail, 'position should count the whole list'
    assert 'This is the last in the list' not in detail, 'stepping stopped at the page edge'


# ── the hierarchical lists ─────────────────────────────────────────────────

def test_a_tree_is_never_split_from_its_parent():
    rows = [('r1', 0), ('c1', 1), ('c2', 1), ('r2', 0), ('c3', 1), ('r3', 0)]
    first = paginate_tree(rows, 1, per_page=2)
    second = paginate_tree(rows, 2, per_page=2)

    assert [n for n, _ in first.items] == ['r1', 'c1', 'c2', 'r2', 'c3']
    assert [n for n, _ in second.items] == ['r3']
    # every page starts at a root, so nothing is indented under nothing
    for page in (first, second):
        assert page.items[0][1] == 0


def test_the_tree_count_is_of_roots_not_rows():
    """A page holds `per_page` top-level records; a deeply nested one makes its
    page longer, which is the right trade for a list drawn as a tree."""
    rows = [('r1', 0), ('c1', 1), ('c2', 1), ('r2', 0)]
    page = paginate_tree(rows, 1, per_page=10)
    assert page.total == 2
    assert len(page.items) == 4


def test_the_asset_list_counts_records_and_keeps_the_tree(signed_in, db):
    """Replaced a weaker version that passed on any of three loose conditions,
    and so survived a change that invalidated all three."""
    loc = create_location(name='Shed')
    parent = create_asset(name='Aparent', location_id=loc.id)
    child = create_asset(name='Zsub', location_id=loc.id, parent_id=parent.id)

    body = signed_in.get('/assets/').get_data(as_text=True)
    assert '2 assets' in body, 'the count should be records, not roots'

    # the child still renders under its parent, not as a root
    assert body.index(f'/assets/{parent.id}') < body.index(f'/assets/{child.id}')


# ── the pager in the header ────────────────────────────────────────────────

def test_the_count_shows_even_on_a_single_page(signed_in, db):
    """"How many are there" does not stop being interesting because they all
    fit on one screen."""
    for i in range(3):
        create_work_order(title=f'Only {i}', status='open')
    bar = filter_bar(signed_in.get('/work-orders/').get_data(as_text=True))
    assert '3 work orders' in bar
    assert 'wo-step' not in bar, 'arrows shown with nowhere to go'


def test_the_top_pager_lives_in_the_filter_bar(signed_in, many):
    """It belongs with the controls that decide what is being counted, not
    beside the New button."""
    body = signed_in.get('/work-orders/').get_data(as_text=True)
    bar = filter_bar(body)
    assert 'list-count' in bar, 'the pager is not in the filter bar'
    assert f'{many} work orders' in bar
    assert 'wo-step' in bar, 'no arrows in the filter bar'

    header = body[body.index('class="page-actions"'):body.index('<form method="get"')]
    assert 'list-count' not in header, 'a copy is still in the page header'


def test_it_is_in_the_filter_bar_on_every_list(signed_in, records):
    for url in ('/work-orders/', '/pms/', '/job-plans/', '/assets/', '/locations/'):
        bar = filter_bar(signed_in.get(url).get_data(as_text=True))
        assert 'list-count' in bar, url


def test_it_is_pinned_right_even_when_the_filters_wrap(signed_in):
    """`margin-left:auto` rather than `justify-content`, so it still sits right
    on its own line once the bar wraps onto two rows."""
    css = (ROOT / 'app' / 'static' / 'css' / 'main.css').read_text()
    assert '.filters .list-count { margin-left: auto; }' in css


def test_both_pagers_say_the_same_thing(signed_in, many):
    """Two copies of "which page am I on" drift — the work order list already
    said Newer/Older where every other list said Previous/Next. One macro now
    renders both."""
    body = signed_in.get('/work-orders/').get_data(as_text=True)
    positions = [' '.join(chunk.split('</span>')[0].split())
                 for chunk in body.split('class="pager-position">')[1:]]
    assert len(positions) == 2, positions
    assert positions[0] == positions[1], positions


def test_no_list_says_newer_or_older(signed_in, many):
    """Consistency with the record stepper, which says Previous and Next."""
    for url in ('/work-orders/', '/pms/', '/job-plans/', '/assets/', '/locations/'):
        body = signed_in.get(url).get_data(as_text=True)
        assert 'Newer' not in body and 'Older' not in body, url


def test_a_tree_list_counts_records_not_roots(signed_in, db):
    """`total` is roots, because that is what a page holds — but "5 top-level
    locations" above a list of 298 reads as a bug, so the visible count is the
    record count."""
    outer = create_location(name='Aouter')
    for i in range(4):
        create_location(name=f'Inner {i}', parent_id=outer.id)

    body = signed_in.get('/locations/').get_data(as_text=True)
    assert '5 locations' in body, 'tree list counted roots instead of records'
    assert '1 location ' not in body
