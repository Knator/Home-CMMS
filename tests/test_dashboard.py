"""The four figures across the top of the dashboard.

What is on my plate, what is late, what is about to be late, what is coming.
The first three open the work order list with the filters that produce the same
number — and the numbers are built from the same clauses on the model as those
filters, because a card that says 3 and opens a list of 4 is worse than a card
that opens nothing.
"""
import html
import itertools
import re
from datetime import date, timedelta

import pytest

from app.extensions import db as _db
from app.models.pm import PM
from app.models.work_order import OVERDUE_SOON_DAYS, WorkOrder
from app.services import create_work_order


def page(client, url='/'):
    return client.get(url).get_data(as_text=True)


def labels(client):
    return [l.strip() for l in re.findall(r'class="stat-label">([^<]+)<', page(client))]


def value_for(client, label):
    """Matched on the class attribute's *start*: a non-zero value also carries
    `danger` or `warning`, which an exact match on `stat-value` would miss."""
    found = re.search(
        re.escape(f'class="stat-label">{label}<') +
        r'.*?class="stat-value[^"]*">\s*(\d+)\s*<', page(client), re.S)
    assert found, f'no value for {label!r}'
    return int(found.group(1))


def card_link(client, label):
    found = re.search(
        r'<a class="stat-card stat-link"\s+href="([^"]+)">\s*<div class="stat-label">'
        + re.escape(label) + '<', page(client))
    assert found, f'{label!r} is not a link'
    return html.unescape(found.group(1))


def list_total(client, url):
    pos = re.search(r'class="pager-position">(.*?)</span>', page(client, url), re.S)
    text = html.unescape(' '.join(pos.group(1).split()))
    return int(re.search(r'(\d+) work orders?$', text).group(1))


SOON = f'Overdue in the Next {OVERDUE_SOON_DAYS} Days'
LINKED = ['Open Work Orders', 'Overdue Work Orders', SOON]


@pytest.fixture
def signed_in(client, db, user, login):
    login()
    return client


@pytest.fixture
def mixed(db):
    """Every status, due dates either side of today, and different graces —
    the combinations where a hand-rolled count drifts from the rows."""
    today = date.today()
    for offset, grace, status in itertools.product(
            (-30, -8, -2, -1, 0, 1, 3, 6, 7, 8, 30), (0, 2, 10),
            ('open', 'in_progress', 'on_hold', 'completed', 'cancelled')):
        create_work_order(title=f'{status} {offset} {grace}', status=status,
                          due_date=today + timedelta(days=offset),
                          overdue_grace_days=grace,
                          completed_date=today if status == 'completed' else None)
    create_work_order(title='no due date', status='open')


# ── the row of cards ───────────────────────────────────────────────────────

def test_the_cards_read_left_to_right_as_intended(signed_in):
    assert labels(signed_in) == [
        'Open Work Orders', 'Overdue Work Orders', SOON, 'PMs Due in the Next 30 Days']


def test_the_retired_cards_are_gone(signed_in):
    body = page(signed_in)
    assert 'Total Assets' not in body
    assert 'Completed in the Last 30 Days' not in body


# ── a card and the list it opens agree ─────────────────────────────────────

@pytest.mark.parametrize('label', LINKED)
def test_each_card_matches_the_list_it_opens(signed_in, mixed, label):
    """The property the links exist for. Checked over every status, both sides
    of today and several graces, which is where a separately written count used
    to drift — it once left on hold out while every red row counted it."""
    number = value_for(signed_in, label)
    assert number == list_total(signed_in, card_link(signed_in, label)), label


def test_the_pm_card_is_not_a_link(signed_in):
    """Not asked for, and the PM list has no "due within 30 days" filter for it
    to land on — a link to the unfiltered list would show the wrong number."""
    assert not re.search(
        r'<a class="stat-card[^>]*>\s*<div class="stat-label">PMs Due', page(signed_in))


# ── what counts ────────────────────────────────────────────────────────────

def test_on_hold_work_counts_as_open(signed_in, db):
    create_work_order(title='Waiting on a part', status='on_hold')
    create_work_order(title='Being worked', status='in_progress')
    create_work_order(title='Untouched', status='open')
    create_work_order(title='Finished', status='completed', completed_date=date.today())
    assert value_for(signed_in, 'Open Work Orders') == 3


def test_on_hold_work_past_due_counts_as_overdue(signed_in, db):
    """Deliberately the reverse of what this once asserted. The card used to
    leave on hold out, while the list's red rows, the Overdue badge and the
    API all counted it — so the card's number and the list it opened could not
    both be right. One definition now, the model's."""
    create_work_order(title='Parked and late', status='on_hold',
                      due_date=date.today() - timedelta(days=60), overdue_grace_days=0)
    assert value_for(signed_in, 'Overdue Work Orders') == 1


def test_finished_work_is_never_overdue(signed_in, db):
    for status in ('completed', 'cancelled'):
        create_work_order(title=status, status=status,
                          due_date=date.today() - timedelta(days=60),
                          completed_date=date.today() if status == 'completed' else None)
    assert value_for(signed_in, 'Overdue Work Orders') == 0
    assert value_for(signed_in, SOON) == 0


# ── going overdue soon ─────────────────────────────────────────────────────

@pytest.mark.parametrize('due_in,grace,expected', [
    (0, 0, True),     # due today: goes overdue tomorrow
    (-1, 0, False),   # due yesterday: already overdue, counted there instead
    (6, 0, True),     # goes overdue on day 7, the last day of the window
    (7, 0, False),    # goes overdue on day 8
    (-5, 10, True),   # past due, but grace runs out in six days
    (-11, 10, False), # grace ran out yesterday: overdue now
    (1, 10, False),   # within grace for another eleven days
])
def test_the_window_is_by_when_it_goes_overdue(signed_in, db, due_in, grace, expected):
    """By the day it turns red, not the day it is due — with grace periods those
    differ, and this card is about what turns red next."""
    create_work_order(title='Edge', status='open',
                      due_date=date.today() + timedelta(days=due_in),
                      overdue_grace_days=grace)
    assert value_for(signed_in, SOON) == (1 if expected else 0)


def test_overdue_and_overdue_soon_never_overlap(db, mixed):
    today = date.today()
    now = {w.id for w in WorkOrder.query.filter(WorkOrder.overdue_clause(today))}
    soon = {w.id for w in WorkOrder.query.filter(WorkOrder.overdue_soon_clause(today))}
    assert now and soon
    assert not now & soon


# ── SQL and Python say the same thing ──────────────────────────────────────

def test_the_sql_clauses_agree_with_the_model(db):
    """The rows are coloured by is_overdue in Python; the cards and the list
    filter count with SQL. Grace is per record, so the SQL adds each row's own
    grace with SQLite's date() — this checks the two agree across every edge."""
    today = date.today()
    for offset, grace, status in itertools.product(
            range(-15, 16), (0, 1, 3, 10),
            ('open', 'in_progress', 'on_hold', 'completed', 'cancelled')):
        create_work_order(title='x', status=status,
                          due_date=today + timedelta(days=offset), overdue_grace_days=grace)
    rows = WorkOrder.query.all()

    sql_now = {w.id for w in WorkOrder.query.filter(WorkOrder.overdue_clause(today))}
    assert sql_now == {w.id for w in rows if w.is_overdue}

    sql_soon = {w.id for w in WorkOrder.query.filter(WorkOrder.overdue_soon_clause(today))}
    assert sql_soon == {w.id for w in rows if w.is_overdue_soon(today)}


# ── the list end ───────────────────────────────────────────────────────────

def test_the_list_shows_the_due_filter_it_was_opened_with(signed_in, mixed):
    """A visible control, not a hidden parameter, so a list opened from a card
    shows why it is short."""
    body = page(signed_in, '/work-orders/?due=overdue')
    assert re.search(r'<option value="overdue" selected>Overdue</option>', body)


def test_an_unknown_due_value_filters_nothing(signed_in, mixed):
    everything = list_total(signed_in, '/work-orders/')
    assert list_total(signed_in, '/work-orders/?due=whenever') == everything
