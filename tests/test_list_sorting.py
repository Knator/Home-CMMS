"""Sortable headings on every list other than work orders, which has its own
file (test_work_order_sorting.py) from when it was the only one.

The shared machinery is app/sorting.py; what each list adds is its own columns,
and those are where it goes wrong — a count that needs a subquery, a name on
another table, a status that must sort by meaning, a tree that has to give way
to a flat list. Each fixture is built so that the wrong order differs from the
right one: mixed case where text must ignore it, statuses whose alphabetical
order is not their workflow order, and a blank in every column that can have one.
"""
import re
from datetime import date, datetime, timedelta

import pytest

from app.extensions import db as _db
from app.models.auth_attempt import AuthAttempt
from app.models.job_plan import JobPlan, JobPlanItem, JobPlanTask
from app.models.mixins import ITEM_MATERIAL, ITEM_TOOL
from app.models.pm import PM
from app.navigation import PAGE_SIZE
from app.services import create_asset, create_location
from app.sorting import SortKey, Sorting, chosen_sort
from app.utils import utcnow
from tests.conftest import make_user, table_headings

TODAY = date.today()


@pytest.fixture
def signed_in(client, db, login):
    make_user('boss', role='super_admin', password='Password123!')
    login('boss', 'Password123!')
    return client


def tbody(body):
    return body[body.index('<tbody>'):body.index('</tbody>')]


def ids_in_order(client, url, path):
    """Record ids in the order the table lists them, read off links to `path`."""
    body = client.get(url).get_data(as_text=True)
    seen = []
    for found in re.findall(re.escape(path) + r'(\d+)[/?"]', tbody(body)):
        if int(found) not in seen:
            seen.append(int(found))
    return seen


def names_in_order(client, url, path, model):
    by_id = {row.id: row for row in model.query}
    return [by_id[i].name for i in ids_in_order(client, url, path)]


def heading(body, key):
    match = re.search(r'<th class="sortable[^"]*"[^>]*>\s*<a href="[^"]*[?&;]sort=' + key
                      + r'[&"].*?</th>', body, re.S)
    assert match, f'no sortable heading for {key}'
    return match.group(0)


def heading_href(body, key):
    return re.search(r'href="([^"]*)"', heading(body, key)).group(1).replace('&amp;', '&')


def filter_form(body):
    start = body.index('<form method="get" class="filters">')
    return body[start:body.index('</form>', start)]


# ── job plans ──────────────────────────────────────────────────────────────

@pytest.fixture
def plans(db):
    def plan(name, description, minutes, materials, tools):
        jp = JobPlan(name=name, description=description)
        _db.session.add(jp)
        _db.session.flush()
        for i, m in enumerate(minutes):
            _db.session.add(JobPlanTask(job_plan_id=jp.id, sequence=i + 1,
                                        description=f'step {i}', estimated_minutes=m))
        for kind, n in ((ITEM_MATERIAL, materials), (ITEM_TOOL, tools)):
            for i in range(n):
                _db.session.add(JobPlanItem(job_plan_id=jp.id, kind=kind, sequence=i + 1,
                                            description=f'{kind} {i}'))
    # A task with no estimate counts as zero, as JobPlan.total_minutes says.
    plan('Bravo', 'Z desc', [10, 20, None], materials=1, tools=0)
    plan('alpha', None, [60], materials=0, tools=2)
    plan('Charlie', 'b desc', [], materials=2, tools=1)
    _db.session.commit()


@pytest.mark.parametrize('query, expected', [
    # Case-insensitive: a byte comparison puts "alpha" last.
    ('sort=name', ['alpha', 'Bravo', 'Charlie']),
    # Counts and durations open largest first.
    ('sort=tasks', ['Bravo', 'alpha', 'Charlie']),
    ('sort=tasks&dir=asc', ['Charlie', 'alpha', 'Bravo']),
    ('sort=duration', ['alpha', 'Bravo', 'Charlie']),
    ('sort=materials', ['Charlie', 'Bravo', 'alpha']),
    ('sort=tools', ['alpha', 'Charlie', 'Bravo']),
    # Blank first ascending; then "b desc" before "Z desc", ignoring case.
    ('sort=description', ['alpha', 'Charlie', 'Bravo']),
    ('sort=description&dir=desc', ['Bravo', 'Charlie', 'alpha']),
])
def test_job_plans_sort_by_every_column(signed_in, plans, query, expected):
    assert names_in_order(signed_in, f'/job-plans/?{query}', '/job-plans/', JobPlan) == expected


def test_job_plan_counts_match_what_the_list_shows(signed_in, plans):
    """The sort counts in SQL and the cells count in Python; they must agree, or
    the column would visibly be out of order."""
    body = tbody(signed_in.get('/job-plans/?sort=duration').get_data(as_text=True))
    # Tasks and duration: the first two cells holding plain text (the name
    # cell holds a link).
    cells = [re.findall(r'<td[^>]*>([^<]*)</td>', row)[0:2] for row in body.split('<tr')[1:]]
    assert cells == [['1', '1h'], ['3', '30m'], ['0', '—']]


# ── PMs ────────────────────────────────────────────────────────────────────

@pytest.fixture
def pms(db):
    pump = create_asset(name='Pump')
    boiler = create_asset(name='boiler')
    _db.session.add_all([
        PM(name='bravo', asset_id=pump.id, interval_days=30, schedule_from_completion=False,
           next_due_date=TODAY + timedelta(days=5),
           last_generated_date=TODAY - timedelta(days=1), is_active=True),
        PM(name='Alpha', interval_days=7, schedule_from_completion=True,
           next_due_date=TODAY + timedelta(days=1), last_generated_date=None, is_active=False),
        PM(name='charlie', asset_id=boiler.id, interval_days=90, schedule_from_completion=True,
           next_due_date=TODAY + timedelta(days=10),
           last_generated_date=TODAY - timedelta(days=20), is_active=True),
    ])
    _db.session.commit()


@pytest.mark.parametrize('query, expected', [
    ('sort=name', ['Alpha', 'bravo', 'charlie']),
    # No asset first; then "boiler" before "Pump", ignoring case.
    ('sort=asset', ['Alpha', 'charlie', 'bravo']),
    ('sort=interval', ['Alpha', 'bravo', 'charlie']),
    # Fixed interval, then last completion; the two floating ones tie and fall
    # back to the list's own order, soonest due first.
    ('sort=basis', ['bravo', 'Alpha', 'charlie']),
    ('sort=next_due', ['Alpha', 'bravo', 'charlie']),
    ('sort=next_due&dir=desc', ['charlie', 'bravo', 'Alpha']),
    # Most recent first, never-generated last.
    ('sort=last_generated', ['bravo', 'charlie', 'Alpha']),
    ('sort=last_generated&dir=asc', ['Alpha', 'charlie', 'bravo']),
    # Active first.
    ('sort=status', ['bravo', 'charlie', 'Alpha']),
])
def test_pms_sort_by_every_column(signed_in, pms, query, expected):
    assert names_in_order(signed_in, f'/pms/?show=all&{query}', '/pms/', PM) == expected


def test_pms_unsorted_stay_soonest_due_first(signed_in, pms):
    assert names_in_order(signed_in, '/pms/?show=all', '/pms/', PM) == ['Alpha', 'bravo', 'charlie']


# ── assets: a tree until sorted ────────────────────────────────────────────

@pytest.fixture
def assets(db):
    attic = create_location(name='attic')
    basement = create_location(name='Basement')
    furnace = create_asset(name='Furnace', location_id=attic.id, category='HVAC',
                           make='Carrier', model='X', install_date=date(2020, 1, 1))
    create_asset(name='blower', parent_id=furnace.id, status='inactive')
    create_asset(name='Washer', location_id=basement.id, category='appliances',
                 make='bosch', install_date=date(2015, 6, 1), status='decommissioned')


from app.models.asset import Asset  # noqa: E402


@pytest.mark.parametrize('query, expected', [
    ('sort=number', ['Furnace', 'blower', 'Washer']),
    ('sort=number&dir=desc', ['Washer', 'blower', 'Furnace']),
    ('sort=name', ['blower', 'Furnace', 'Washer']),
    # Active, inactive, decommissioned — not alphabetical.
    ('sort=status', ['Furnace', 'blower', 'Washer']),
    ('sort=status&dir=desc', ['Washer', 'blower', 'Furnace']),
    ('sort=category', ['blower', 'Washer', 'Furnace']),
    ('sort=location', ['blower', 'Furnace', 'Washer']),
    # As shown: make then model, ignoring case; a blank one first.
    ('sort=make_model', ['blower', 'Washer', 'Furnace']),
    ('sort=install_date', ['blower', 'Washer', 'Furnace']),
    ('sort=install_date&dir=desc', ['Furnace', 'Washer', 'blower']),
])
def test_assets_sort_by_every_column(signed_in, assets, query, expected):
    assert names_in_order(signed_in, f'/assets/?show=all&{query}', '/assets/', Asset) == expected


def depths(client, url):
    body = tbody(client.get(url).get_data(as_text=True))
    return [int(d) for d in re.findall(r'--depth: (\d+)', body)]


def test_assets_are_a_tree_until_sorted(signed_in, assets):
    """Unsorted, the sub-assembly sits indented under its parent. Sorted, it
    takes its place in the order like anything else — it cannot do both."""
    tree = '/assets/?show=all'
    assert names_in_order(signed_in, tree, '/assets/', Asset) == ['Furnace', 'blower', 'Washer']
    assert depths(signed_in, tree) == [0, 1, 0]

    flat = '/assets/?show=all&sort=name'
    assert names_in_order(signed_in, flat, '/assets/', Asset) == ['blower', 'Furnace', 'Washer']
    assert depths(signed_in, flat) == [0, 0, 0]
    assert 'tree-branch' not in tbody(signed_in.get(flat).get_data(as_text=True))


def show_as_tree(body):
    found = re.search(r'<a href="([^"]*)"\s+class="btn btn-ghost btn-sm">Show as tree</a>', body)
    return found.group(1).replace('&amp;', '&') if found else None


@pytest.mark.parametrize('path', ['/assets/', '/locations/'])
def test_show_as_tree_drops_the_order_and_keeps_the_filters(signed_in, assets, path):
    assert show_as_tree(signed_in.get(f'{path}?show=all').get_data(as_text=True)) is None
    link = show_as_tree(signed_in.get(f'{path}?show=all&q=a&sort=name&dir=desc')
                        .get_data(as_text=True))
    assert link is not None
    assert 'sort=' not in link and 'dir=' not in link
    assert 'show=all' in link and 'q=a' in link


def stepper_targets(body):
    prev = re.search(r'href="/\w[\w-]*/(\d+)[^"]*"\s+class="btn btn-secondary wo-step" rel="prev"', body)
    nxt = re.search(r'href="/\w[\w-]*/(\d+)[^"]*"\s+class="btn btn-secondary wo-step" rel="next"', body)
    return (int(prev.group(1)) if prev else None, int(nxt.group(1)) if nxt else None)


def test_an_assets_arrows_follow_the_sorted_list(signed_in, assets):
    ids = {a.name: a.id for a in Asset.query}
    # The tree order: Furnace, blower, Washer.
    body = signed_in.get(f'/assets/{ids["Furnace"]}?show=all').get_data(as_text=True)
    assert stepper_targets(body) == (None, ids['blower'])
    # By name: blower, Furnace, Washer.
    body = signed_in.get(f'/assets/{ids["Furnace"]}?show=all&sort=name').get_data(as_text=True)
    assert stepper_targets(body) == (ids['blower'], ids['Washer'])


# ── locations ──────────────────────────────────────────────────────────────

from app.models.location import Location  # noqa: E402


@pytest.fixture
def locations(db):
    house = create_location(name='house', description='Main')
    kitchen = create_location(name='Kitchen', parent_id=house.id, status='inactive')
    garage = create_location(name='Garage', description='detached', status='decommissioned')
    create_asset(name='Fridge', location_id=kitchen.id)
    create_asset(name='Kettle', location_id=kitchen.id)
    create_asset(name='Mower', location_id=garage.id)


@pytest.mark.parametrize('query, expected', [
    ('sort=number', ['house', 'Kitchen', 'Garage']),
    ('sort=name', ['Garage', 'house', 'Kitchen']),
    ('sort=status', ['house', 'Kitchen', 'Garage']),
    ('sort=description', ['Kitchen', 'Garage', 'house']),
    # Most assets first; the count includes every asset there.
    ('sort=assets', ['Kitchen', 'Garage', 'house']),
    ('sort=assets&dir=asc', ['house', 'Garage', 'Kitchen']),
])
def test_locations_sort_by_every_column(signed_in, locations, query, expected):
    assert names_in_order(signed_in, f'/locations/?show=all&{query}', '/locations/', Location) == expected


def test_locations_are_a_tree_until_sorted(signed_in, locations):
    assert depths(signed_in, '/locations/?show=all') == [0, 0, 1]   # Garage, house, Kitchen under it
    assert depths(signed_in, '/locations/?show=all&sort=assets') == [0, 0, 0]


# ── users ──────────────────────────────────────────────────────────────────

from app.models.user import User  # noqa: E402


@pytest.fixture
def people(signed_in):
    def person(username, **fields):
        u = make_user(username, password='Password123!')
        for name, value in fields.items():
            setattr(u, name, value)
        return u
    person('zed', display_name='Amy', email='Zed@x.example',
           last_login=utcnow() - timedelta(days=2))
    person('bob', email='alpha@x.example', role='admin', is_active=False, last_login=None)
    User.query.filter_by(username='boss').one().last_login = utcnow()
    _db.session.commit()
    return signed_in


def usernames(client, url):
    by_id = {u.id: u.username for u in User.query}
    return [by_id[i] for i in ids_in_order(client, url, '/admin/users/')]


@pytest.mark.parametrize('query, expected', [
    # By what the list shows: zed is called Amy.
    ('', ['zed', 'bob', 'boss']),
    ('sort=name', ['zed', 'bob', 'boss']),
    ('sort=name&dir=desc', ['boss', 'bob', 'zed']),
    ('sort=email', ['bob', 'boss', 'zed']),
    # By rank, not alphabetically: super admin, admin, user.
    ('sort=role', ['boss', 'bob', 'zed']),
    ('sort=role&dir=desc', ['zed', 'bob', 'boss']),
    ('sort=status', ['zed', 'boss', 'bob']),
    # Most recent first, never signed in last — and first the other way round.
    ('sort=last_login', ['boss', 'zed', 'bob']),
    ('sort=last_login&dir=asc', ['bob', 'zed', 'boss']),
])
def test_users_sort_by_every_column(people, query, expected):
    assert usernames(people, f'/admin/users?{query}') == expected


def test_an_empty_display_name_sorts_as_the_username(people):
    """The label's `or` treats '' as unset, so the sort must too."""
    u = User.query.filter_by(username='zed').one()
    u.display_name = ''
    _db.session.commit()
    assert usernames(people, '/admin/users?sort=name') == ['bob', 'boss', 'zed']


# ── the sign-in log ────────────────────────────────────────────────────────

@pytest.fixture
def attempts(signed_in):
    now = utcnow()
    _db.session.add_all([
        AuthAttempt(identifier='Carol', ip_address='10.0.0.9', successful=False,
                    created_at=now - timedelta(hours=3)),
        AuthAttempt(identifier='alice', ip_address='10.0.0.10', successful=True,
                    created_at=now - timedelta(hours=1)),
        AuthAttempt(identifier='bob', ip_address=None, successful=False,
                    created_at=now - timedelta(hours=2)),
    ])
    _db.session.commit()
    return signed_in


def accounts(client, url):
    """Identifiers in table order, leaving out the test's own sign-in."""
    body = tbody(client.get(url).get_data(as_text=True))
    found = re.findall(r'<tr>\s*<td>[^<]*</td>\s*<td class="mono-cell">([^<]*)</td>', body)
    return [name for name in found if name != 'boss']


@pytest.mark.parametrize('query, expected', [
    ('', ['alice', 'bob', 'Carol']),                    # newest first, as before
    ('sort=when', ['alice', 'bob', 'Carol']),
    ('sort=when&dir=asc', ['Carol', 'bob', 'alice']),
    ('sort=account', ['alice', 'bob', 'Carol']),        # ignoring case
    # No address first; then as text, which puts .10 before .9.
    ('sort=ip', ['bob', 'alice', 'Carol']),
    # Failures first, newest first among them.
    ('sort=result', ['bob', 'Carol', 'alice']),
])
def test_the_sign_in_log_sorts_by_every_column(attempts, query, expected):
    assert accounts(attempts, f'/admin/sign-in-attempts?outcome=all&{query}') == expected


def test_the_sign_in_pager_carries_the_order(signed_in, db):
    for i in range(PAGE_SIZE + 5):
        _db.session.add(AuthAttempt(identifier=f'user{i:02d}', ip_address='10.1.1.1',
                                    successful=False))
    _db.session.commit()
    body = signed_in.get('/admin/sign-in-attempts?sort=account&dir=asc&ip=10.1.').get_data(as_text=True)
    following = re.search(r'href="([^"]*page=2[^"]*)">Next →</a>', body)
    assert following, 'sorted, the pager reads Previous/Next rather than Newer/Older'
    href = following.group(1).replace('&amp;', '&')
    assert 'sort=account' in href and 'dir=asc' in href and 'ip=10.1.' in href
    second = signed_in.get(href).get_data(as_text=True)
    assert '← Previous' in second and 'Newer' not in second


# ── every list, the same way ──────────────────────────────────────────────

# (url, sortable keys in column order, keys hidden on small screens, a filter
#  that makes Clear appear)
LISTS = {
    'job plans': ('/job-plans/', ['name', 'tasks', 'duration', 'materials', 'tools', 'description'],
                  {'materials', 'tools', 'description'}, 'q=a'),
    'PMs': ('/pms/', ['name', 'asset', 'interval', 'basis', 'next_due', 'last_generated', 'status'],
            {'interval', 'basis', 'last_generated'}, 'show=all'),
    'assets': ('/assets/', ['number', 'name', 'status', 'category', 'location', 'make_model',
                            'install_date'],
               {'category', 'make_model', 'install_date'}, 'show=all'),
    'locations': ('/locations/', ['number', 'name', 'status', 'description', 'assets'],
                  {'description'}, 'show=all'),
    'users': ('/admin/users', ['name', 'email', 'role', 'status', 'last_login'],
              {'email', 'last_login'}, 'role=admin'),
    'sign-in log': ('/admin/sign-in-attempts', ['when', 'account', 'ip', 'result'],
                    set(), 'outcome=all'),
}


@pytest.fixture
def one_of_everything(signed_in, db):
    """Every list needs a row, or it renders its empty state and no headings."""
    loc = create_location(name='Shed')
    create_asset(name='Mower', location_id=loc.id)
    _db.session.add(JobPlan(name='Service'))
    _db.session.add(PM(name='Service PM', interval_days=30, next_due_date=TODAY))
    # The log shows failures by default, and the test's own sign-in succeeded.
    _db.session.add(AuthAttempt(identifier='intruder', ip_address='10.0.0.1', successful=False))
    _db.session.commit()
    return signed_in


@pytest.mark.parametrize('name', LISTS)
def test_every_column_but_the_actions_sorts(one_of_everything, name):
    url, keys, hidden, _filter = LISTS[name]
    body = one_of_everything.get(url).get_data(as_text=True)
    sortable = re.findall(r'<th class="sortable[^"]*"[^>]*>\s*<a href="[^"]*[?&;]sort=(\w+)', body)
    assert sortable == keys
    # Nothing is the current order until a heading is clicked.
    assert 'aria-sort' not in body
    for key in keys:
        opening = heading(body, key).split('>')[0]
        assert ('col-hide-sm' in opening) == (key in hidden), key


@pytest.mark.parametrize('name', LISTS)
def test_clicking_a_heading_marks_it_and_offers_the_reverse(one_of_everything, name):
    url, keys, _hidden, _filter = LISTS[name]
    key = keys[1]
    first = heading_href(one_of_everything.get(url).get_data(as_text=True), key)
    direction = re.search(r'dir=(asc|desc)', first).group(1)
    body = one_of_everything.get(first).get_data(as_text=True)
    assert body.count('aria-sort') == 1
    assert f'aria-sort="{"ascending" if direction == "asc" else "descending"}"' in heading(body, key)
    reverse = 'desc' if direction == 'asc' else 'asc'
    assert f'dir={reverse}' in heading_href(body, key)


@pytest.mark.parametrize('name', LISTS)
def test_the_filter_form_and_clear_keep_the_order(one_of_everything, name):
    url, keys, _hidden, filter_arg = LISTS[name]
    key = keys[-1]
    form = filter_form(one_of_everything.get(f'{url}?{filter_arg}&sort={key}&dir=asc')
                       .get_data(as_text=True))
    assert f'<input type="hidden" name="sort" value="{key}">' in form
    assert '<input type="hidden" name="dir" value="asc">' in form
    clear = re.search(r'<a href="([^"]*)" class="btn btn-ghost btn-sm">Clear</a>', form)
    clear = clear.group(1).replace('&amp;', '&')
    assert f'sort={key}' in clear and 'dir=asc' in clear
    assert filter_arg not in clear


@pytest.mark.parametrize('name', LISTS)
def test_an_unknown_sort_is_ignored(one_of_everything, name):
    url = LISTS[name][0]
    response = one_of_everything.get(f'{url}?sort=id;drop&dir=sideways')
    assert response.status_code == 200
    assert 'aria-sort' not in response.get_data(as_text=True)


@pytest.mark.parametrize('name', ['job plans', 'PMs', 'assets', 'locations', 'users'])
def test_rows_carry_the_order_to_the_record(one_of_everything, name):
    url, keys, _hidden, _filter = LISTS[name]
    body = one_of_everything.get(f'{url}?sort={keys[0]}&dir=desc').get_data(as_text=True)
    path = url.rstrip('/') + '/'
    links = [h for h in re.findall(r'href="([^"]*)"', tbody(body)) if h.startswith(path)]
    assert links
    assert all(f'sort={keys[0]}' in h and 'dir=desc' in h for h in links)


# ── the shared pieces ─────────────────────────────────────────────────────

SORTS = {'due': SortKey(lambda: None), 'priority': SortKey(lambda: None, 'desc')}


def test_a_heading_goes_its_own_way_first_and_reverses_when_current():
    sorting = chosen_sort({'sort': 'due', 'dir': 'asc'}, SORTS)
    assert sorting.next_direction('due') == 'desc'
    assert sorting.next_direction('priority') == 'desc'   # its own first direction
    assert chosen_sort({'sort': 'due', 'dir': 'desc'}, SORTS).next_direction('due') == 'asc'


def test_a_bad_direction_uses_the_columns_own():
    assert chosen_sort({'sort': 'priority', 'dir': 'up'}, SORTS).direction == 'desc'
    assert chosen_sort({'sort': 'due'}, SORTS).direction == 'asc'


def test_an_unknown_key_means_unsorted():
    sorting = chosen_sort({'sort': 'nope', 'dir': 'asc'}, SORTS)
    assert not sorting
    assert sorting.args() == {}


def test_the_order_can_be_taken_out_of_the_carried_arguments():
    carried = {'q': ['pump'], 'sort': ['due'], 'dir': ['asc'], 'show': ['all']}
    assert Sorting.without(carried) == {'q': ['pump'], 'show': ['all']}
    assert chosen_sort({'sort': 'due', 'dir': 'desc'}, SORTS).args() == {'sort': 'due', 'dir': 'desc'}
