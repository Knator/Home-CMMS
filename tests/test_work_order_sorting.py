"""Clicking a work order list heading sorts the whole list, not just one page.

The list is paged, so the order has to come from the database before the page
is cut — sorting in the browser would only rearrange the fifty rows it holds.
These pin the parts that are easy to get subtly wrong: status and priority by
meaning rather than spelling, blanks reachable from either end, a
tiebreaker so a row cannot appear on two pages, and the order surviving the
filter form, the pager and a trip to a record and back.
"""
import re
from datetime import date, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db as _db
from app.models.work_order import WorkOrder, WO_PRIORITIES, WO_STATUSES
from app.navigation import PAGE_SIZE
from app.services import create_asset, create_location, create_work_order
from tests.conftest import make_user, prime_csrf

TODAY = date.today()


@pytest.fixture
def signed_in(client, db, login):
    make_user('tester', role='admin', password='Password123!')
    login('tester', 'Password123!')
    return client


def order_on(client, url):
    """Titles in the order the table shows them."""
    body = client.get(url).get_data(as_text=True)
    table = body[body.index('<tbody>'):body.index('</tbody>')]
    titles = []
    for row in table.split('<tr')[1:]:
        # The title is the second row link; the first is the number.
        links = re.findall(r'<a class="row-link" href="/work-orders/\d+[^"]*">([^<]*)</a>', row)
        titles.append(links[1])
    return titles


def ids_on(client, url):
    body = client.get(url).get_data(as_text=True)
    table = body[body.index('<tbody>'):body.index('</tbody>')]
    seen = []
    for found in re.findall(r'href="/work-orders/(\d+)\?', table):
        if int(found) not in seen:
            seen.append(int(found))
    return seen


def heading(body, key):
    """The <th> for one sort key."""
    match = re.search(r'<th class="sortable[^"]*"[^>]*>\s*<a href="[^"]*sort=' + key + r'[&"].*?</th>',
                      body, re.S)
    assert match, f'no sortable heading for {key}'
    return match.group(0)


def heading_href(body, key):
    return re.search(r'href="([^"]*)"', heading(body, key)).group(1).replace('&amp;', '&')


# ── the order itself ────────────────────────────────────────────────────────

@pytest.fixture
def varied(db):
    """Four work orders that disagree with each other on almost every column."""
    shed = create_location(name='Shed')
    attic = create_location(name='attic')
    mower = create_asset(name='Mower', location_id=shed.id)
    boiler = create_asset(name='boiler', location_id=attic.id)
    create_work_order(title='bravo', wo_type='unplanned', status='completed', priority='low',
                      asset_id=mower.id, location_id=shed.id,
                      due_date=TODAY + timedelta(days=5), completed_date=TODAY - timedelta(days=1))
    create_work_order(title='alpha', wo_type='planned', status='on_hold', priority='critical',
                      asset_id=boiler.id, location_id=attic.id,
                      due_date=TODAY - timedelta(days=2))
    create_work_order(title='Charlie', wo_type='planned', status='open', priority='medium',
                      completed_date=None, due_date=None)
    # Completed, to give the Completed column a second date to order.
    create_work_order(title='delta', wo_type='unplanned', status='completed', priority='high',
                      due_date=TODAY, completed_date=TODAY - timedelta(days=9))


ALL = '&status=' + '&status='.join(WO_STATUSES)


@pytest.mark.parametrize('key, ascending', [
    # Case-insensitive: "Charlie" is not sorted ahead of "alpha" for being
    # capitalised, as a plain byte comparison would.
    ('title', ['alpha', 'bravo', 'Charlie', 'delta']),
    # Numbers are allocated in creation order.
    ('number', ['bravo', 'alpha', 'Charlie', 'delta']),
    # Planned before unplanned; within each, the tie falls back to newest first.
    ('type', ['Charlie', 'alpha', 'delta', 'bravo']),
    # Workflow order, not alphabetical (which would put completed first). The
    # two completed ones tie, and fall back to newest first.
    ('status', ['Charlie', 'alpha', 'delta', 'bravo']),
    # Severity, not alphabetical (which would be critical, high, low, medium).
    ('priority', ['bravo', 'Charlie', 'delta', 'alpha']),
    # By name, case-insensitively. Blanks count as lowest, so ascending puts
    # the work orders with no asset (or location, due or completed date) first.
    ('asset', ['Charlie', 'delta', 'alpha', 'bravo']),
    ('location', ['Charlie', 'delta', 'alpha', 'bravo']),
    ('due', ['Charlie', 'alpha', 'delta', 'bravo']),
    ('completed', ['Charlie', 'alpha', 'delta', 'bravo']),
])
def test_each_heading_orders_the_list(signed_in, varied, key, ascending):
    got = order_on(signed_in, f'/work-orders/?sort={key}&dir=asc{ALL}')
    if key in ('asset', 'location', 'completed'):
        # Two work orders are blank; their order between themselves is the
        # tiebreak's business, but both must come before every filled one.
        assert set(got[:2]) == set(ascending[:2])
        assert got[2:] == ascending[2:]
    else:
        assert got == ascending


def test_status_follows_the_workflow_not_the_alphabet(signed_in, db):
    for status in reversed(WO_STATUSES):
        create_work_order(title=status, status=status)
    assert order_on(signed_in, f'/work-orders/?sort=status&dir=asc{ALL}') == WO_STATUSES
    assert order_on(signed_in, f'/work-orders/?sort=status&dir=desc{ALL}') == WO_STATUSES[::-1]


def test_priority_follows_severity_not_the_alphabet(signed_in, db):
    for priority in ('medium', 'critical', 'low', 'high'):
        create_work_order(title=priority, priority=priority)
    assert order_on(signed_in, '/work-orders/?sort=priority&dir=asc') == WO_PRIORITIES
    assert order_on(signed_in, '/work-orders/?sort=priority&dir=desc') == WO_PRIORITIES[::-1]


def test_blanks_lead_ascending_and_trail_descending(signed_in, db):
    """Blanks count as the lowest value, so they move with the order rather than
    being pinned to the bottom — one click brings them all to the top."""
    create_work_order(title='none')
    create_work_order(title='early', due_date=TODAY - timedelta(days=3))
    create_work_order(title='late', due_date=TODAY + timedelta(days=3))
    assert order_on(signed_in, '/work-orders/?sort=due&dir=asc') == ['none', 'early', 'late']
    assert order_on(signed_in, '/work-orders/?sort=due&dir=desc') == ['late', 'early', 'none']


def test_work_orders_with_no_asset_can_be_brought_to_the_top(signed_in, db):
    """What the change was for: finding everything that was never tied to an
    asset, without paging to the end of the list."""
    pump = create_asset(name='Pump')
    for i in range(PAGE_SIZE + 5):
        create_work_order(title=f'Pump job {i}', asset_id=pump.id)
    create_work_order(title='orphan one')
    create_work_order(title='orphan two')
    first_page = order_on(signed_in, '/work-orders/?sort=asset&dir=asc')
    assert set(first_page[:2]) == {'orphan one', 'orphan two'}
    # And the other way round they are at the very end, not on page one.
    assert 'orphan one' not in order_on(signed_in, '/work-orders/?sort=asset&dir=desc')
    last_page = order_on(signed_in, '/work-orders/?sort=asset&dir=desc&page=2')
    assert set(last_page[-2:]) == {'orphan one', 'orphan two'}


def test_an_unknown_key_falls_back_to_newest_first(signed_in, db):
    for title in ('first', 'second', 'third'):
        create_work_order(title=title)
    response = signed_in.get('/work-orders/?sort=wo_number;drop&dir=asc')
    assert response.status_code == 200
    assert order_on(signed_in, '/work-orders/?sort=nonsense&dir=asc') == ['third', 'second', 'first']
    assert order_on(signed_in, '/work-orders/') == ['third', 'second', 'first']


def test_an_unknown_direction_uses_the_columns_own(signed_in, db):
    create_work_order(title='later', due_date=TODAY + timedelta(days=1))
    create_work_order(title='sooner', due_date=TODAY)
    # Due's first click is soonest first.
    assert order_on(signed_in, '/work-orders/?sort=due&dir=sideways') == ['sooner', 'later']


# ── paging ──────────────────────────────────────────────────────────────────

def test_ties_cannot_put_a_row_on_two_pages(signed_in, db):
    """Every row the same status: the sort key decides nothing, so without a
    unique tiebreaker the database may order them differently for each page."""
    total = PAGE_SIZE + 15
    for i in range(total):
        create_work_order(title=f'Job {i}', status='open')
    every = {wo.id for wo in WorkOrder.query}
    for direction in ('asc', 'desc'):
        first = ids_on(signed_in, f'/work-orders/?sort=status&dir={direction}')
        second = ids_on(signed_in, f'/work-orders/?sort=status&dir={direction}&page=2')
        assert len(first) == PAGE_SIZE
        assert not set(first) & set(second)
        assert set(first) | set(second) == every


def test_the_second_page_continues_the_sort(signed_in, db):
    for i in range(PAGE_SIZE + 5):
        create_work_order(title=f'Job {i:03d}', due_date=TODAY + timedelta(days=i))
    first = order_on(signed_in, '/work-orders/?sort=due&dir=desc')
    second = order_on(signed_in, '/work-orders/?sort=due&dir=desc&page=2')
    assert first[0] == f'Job {PAGE_SIZE + 4:03d}'
    assert second == [f'Job {i:03d}' for i in range(4, -1, -1)]


def test_the_regular_expression_path_keeps_the_order(signed_in, db):
    """That path loads rows and matches in Python, so it is a separate place the
    order could be lost."""
    create_work_order(title='pump b', priority='low')
    create_work_order(title='pump a', priority='critical')
    create_work_order(title='valve', priority='high')
    assert order_on(signed_in, '/work-orders/?sort=title&dir=asc&regex=1&q=^pump') == ['pump a', 'pump b']
    assert order_on(signed_in, '/work-orders/?sort=priority&dir=desc&regex=1&q=^pump') == ['pump a', 'pump b']
    assert order_on(signed_in, '/work-orders/?sort=priority&dir=asc&regex=1&q=^pump') == ['pump b', 'pump a']


# ── the headings ────────────────────────────────────────────────────────────

def test_a_first_click_goes_the_columns_preferred_way(signed_in, db):
    create_work_order(title='x')
    body = signed_in.get('/work-orders/').get_data(as_text=True)
    assert 'dir=asc' in heading_href(body, 'due')          # soonest first
    assert 'dir=desc' in heading_href(body, 'priority')    # critical first
    assert 'dir=desc' in heading_href(body, 'completed')   # most recent first
    assert 'dir=asc' in heading_href(body, 'title')
    # Nothing is marked as the current order while the list is unsorted.
    assert 'aria-sort' not in body


def test_clicking_the_current_heading_reverses_it(signed_in, db):
    create_work_order(title='x')
    body = signed_in.get('/work-orders/?sort=due&dir=asc').get_data(as_text=True)
    assert 'dir=desc' in heading_href(body, 'due')
    assert 'aria-sort="ascending"' in heading(body, 'due')
    assert body.count('aria-sort') == 1

    body = signed_in.get('/work-orders/?sort=due&dir=desc').get_data(as_text=True)
    assert 'dir=asc' in heading_href(body, 'due')
    assert 'aria-sort="descending"' in heading(body, 'due')


def test_only_the_active_headings_arrow_is_lit(signed_in, db):
    create_work_order(title='x')
    body = signed_in.get('/work-orders/?sort=priority&dir=desc').get_data(as_text=True)
    assert body.count('class="on"') == 1
    lit = heading(body, 'priority')
    # The second path is the downward triangle.
    assert re.search(r'<path d="M5 1[^"]*"/>\s*<path class="on" d="M5 13', lit)


def test_headings_keep_the_filters_and_drop_the_page(signed_in, db):
    """Changing the order rearranges the whole list, so staying on page three
    would land somewhere arbitrary."""
    for i in range(PAGE_SIZE + 1):
        create_work_order(title=f'x {i}', status='open')
    body = signed_in.get('/work-orders/?status=open&q=x&page=2').get_data(as_text=True)
    href = heading_href(body, 'title')
    assert 'status=open' in href and 'q=x' in href
    assert 'page=' not in href


def test_headings_keep_the_columns_that_hide_on_small_screens(signed_in, db):
    create_work_order(title='x')
    body = signed_in.get('/work-orders/').get_data(as_text=True)
    for key in ('type', 'priority', 'asset', 'location', 'completed'):
        assert 'col-hide-sm' in heading(body, key).split('>')[0]
    for key in ('number', 'title', 'status', 'due'):
        assert 'col-hide-sm' not in heading(body, key).split('>')[0]


# ── the order survives everything else ─────────────────────────────────────

def filter_form(body):
    start = body.index('<form method="get" class="filters">')
    return body[start:body.index('</form>', start)]


def test_the_filter_form_carries_the_sort(signed_in, db):
    """A GET form builds its query string from its own fields only, so without
    these applying a filter would quietly discard the order."""
    create_work_order(title='x')
    form = filter_form(signed_in.get('/work-orders/?sort=due&dir=desc').get_data(as_text=True))
    assert '<input type="hidden" name="sort" value="due">' in form
    assert '<input type="hidden" name="dir" value="desc">' in form

    form = filter_form(signed_in.get('/work-orders/').get_data(as_text=True))
    assert 'name="sort"' not in form


def test_clear_drops_the_filters_but_keeps_the_order(signed_in, db):
    create_work_order(title='x', status='open')
    form = filter_form(signed_in.get('/work-orders/?status=open&sort=due&dir=desc').get_data(as_text=True))
    clear = re.search(r'<a href="([^"]*)"\s+class="btn btn-ghost btn-sm">Clear</a>', form).group(1)
    assert clear.replace('&amp;', '&') in ('/work-orders/?sort=due&dir=desc', '/work-orders/?dir=desc&sort=due')


def test_the_empty_state_keeps_the_order_too(signed_in, db):
    create_work_order(title='x', status='open')
    body = signed_in.get('/work-orders/?q=nothing-matches&sort=due&dir=desc').get_data(as_text=True)
    clear = re.search(r'<a href="([^"]*)">Clear them</a>', body).group(1).replace('&amp;', '&')
    assert 'sort=due' in clear and 'dir=desc' in clear and 'q=' not in clear


def test_rows_and_pager_carry_the_sort(signed_in, db):
    for i in range(PAGE_SIZE + 1):
        create_work_order(title=f'Job {i}')
    body = signed_in.get('/work-orders/?sort=title&dir=desc').get_data(as_text=True)
    table = body[body.index('<tbody>'):body.index('</tbody>')]
    rows = re.findall(r'href="(/work-orders/\d+[^"]*)"', table)
    assert rows and all('sort=title' in r and 'dir=desc' in r for r in rows)
    pager = re.findall(r'href="([^"]*page=2[^"]*)"', body)
    assert pager and all('sort=title' in p for p in pager)


def test_previous_and_next_on_a_record_follow_the_sort(signed_in, db):
    """The record's arrows walk the list it was opened from — in that order."""
    create_work_order(title='b', due_date=TODAY + timedelta(days=2))
    create_work_order(title='a', due_date=TODAY + timedelta(days=1))
    create_work_order(title='c', due_date=TODAY + timedelta(days=3))
    by_title = {wo.title: wo.id for wo in WorkOrder.query}

    body = signed_in.get(f'/work-orders/{by_title["b"]}?sort=due&dir=asc').get_data(as_text=True)
    previous = re.search(r'href="/work-orders/(\d+)[^"]*"\s+class="btn btn-secondary wo-step" rel="prev"', body)
    following = re.search(r'href="/work-orders/(\d+)[^"]*"\s+class="btn btn-secondary wo-step" rel="next"', body)
    assert int(previous.group(1)) == by_title['a']
    assert int(following.group(1)) == by_title['c']
    assert '2 of 3' in body

    body = signed_in.get(f'/work-orders/{by_title["a"]}?sort=due&dir=desc').get_data(as_text=True)
    assert '3 of 3' in body


def test_saving_an_edit_returns_to_the_sorted_list(signed_in, db):
    wo = create_work_order(title='x', status='open', priority='low')
    prime_csrf(signed_in)
    with signed_in.session_transaction() as sess:
        token = sess['csrf_token']
    response = signed_in.post(
        f'/work-orders/{wo.id}/edit?sort=priority&dir=desc',
        data=MultiDict({'csrf_token': token, 'title': 'x', 'status': 'open',
                        'priority': 'low', 'wo_type': 'planned'}),
    )
    assert response.status_code == 302
    location = response.headers['Location']
    assert 'sort=priority' in location and 'dir=desc' in location
