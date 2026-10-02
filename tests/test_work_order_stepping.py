"""Opening a work order from a filtered list, and stepping through it.

The filters travel in the query string rather than a session, so the position
survives a reload, a bookmark and a shared link — and two people looking at the
same URL see the same sequence.
"""
from datetime import date, timedelta

import pytest

from app.services import create_work_order
from tests.conftest import make_user


@pytest.fixture
def signed_in(client, db, login):
    make_user('tester', role='admin', password='Password123!')
    login('tester', 'Password123!')
    return client


@pytest.fixture
def three(db):
    """Newest first is the list order, so these come back c, b, a."""
    a = create_work_order(title='Alpha pump', status='open', priority='low')
    b = create_work_order(title='Beta valve', status='open', priority='high')
    c = create_work_order(title='Gamma pump', status='completed',
                          priority='low', completed_date=date.today())
    return a, b, c


def links(body, text):
    """Every href on an element whose markup contains `text`."""
    out = []
    for chunk in body.split('<a ')[1:]:
        tag = chunk[:chunk.index('</a>')] if '</a>' in chunk else chunk[:300]
        if text in tag:
            out.append(tag.split('href="', 1)[1].split('"', 1)[0])
    return out


# ── the filters survive opening a record ───────────────────────────────────

def test_row_links_carry_the_filters(signed_in, three):
    body = signed_in.get('/work-orders/?status=open&q=pump').get_data(as_text=True)
    hrefs = [h for h in links(body, 'row-link') if '/work-orders/' in h]
    assert hrefs, 'no row links at all'
    assert all('status=open' in h and 'q=pump' in h for h in hrefs), hrefs


def test_back_returns_to_the_filtered_list(signed_in, three):
    a, b, c = three
    body = signed_in.get(f'/work-orders/{a.id}?status=open&q=pump').get_data(as_text=True)
    # Matched on the class, not the arrow glyph: the markup may spell that as a
    # character or an entity, and the test is about where the link goes.
    back = [h for h in links(body, 'page-back')]
    assert back, 'no Back link'
    assert 'status=open' in back[0] and 'q=pump' in back[0], back


def test_back_is_plain_when_nothing_was_filtered(signed_in, three):
    a, _, _ = three
    body = signed_in.get(f'/work-orders/{a.id}').get_data(as_text=True)
    assert links(body, 'page-back')[0].rstrip('/').endswith('/work-orders')


# ── stepping ───────────────────────────────────────────────────────────────

def test_the_middle_record_can_go_both_ways(signed_in, three):
    a, b, c = three                      # list order: c, b, a
    body = signed_in.get(f'/work-orders/{b.id}').get_data(as_text=True)
    assert f'/work-orders/{c.id}' in body
    assert f'/work-orders/{a.id}' in body
    assert '2 of 3' in body


def test_the_first_record_cannot_go_back(signed_in, three):
    a, b, c = three
    body = signed_in.get(f'/work-orders/{c.id}').get_data(as_text=True)
    assert 'This is the first in the list' in body
    assert '1 of 3' in body


def test_the_last_record_cannot_go_forward(signed_in, three):
    a, b, c = three
    body = signed_in.get(f'/work-orders/{a.id}').get_data(as_text=True)
    assert 'This is the last in the list' in body
    assert '3 of 3' in body


def test_an_end_stop_is_not_a_link(signed_in, three):
    """Greyed out has to mean inert, not merely faint. A <span> carries no href,
    so there is nothing to click and nothing to tab to."""
    a, b, c = three
    body = signed_in.get(f'/work-orders/{c.id}').get_data(as_text=True)
    i = body.index('This is the first in the list')
    tag = body[body.rindex('<', 0, i):body.index('>', i) + 1]
    assert tag.startswith('<span'), tag
    assert 'href' not in tag


def test_stepping_stays_inside_the_filter(signed_in, three):
    """The whole point. Filtered to open work, the completed one must not be
    reachable with an arrow."""
    a, b, c = three                      # c is completed
    body = signed_in.get(f'/work-orders/{b.id}?status=open').get_data(as_text=True)
    assert f'/work-orders/{c.id}' not in body
    assert '1 of 2' in body              # b is now the first of the two open ones


def test_the_arrows_keep_the_filter_for_the_next_hop(signed_in, three):
    a, b, c = three
    body = signed_in.get(f'/work-orders/{b.id}?status=open').get_data(as_text=True)
    hop = [h for h in links(body, 'wo-step') if f'/work-orders/{a.id}' in h]
    assert hop and 'status=open' in hop[0], hop


def test_no_arrows_when_the_record_is_not_in_the_list(signed_in, three):
    """Opening a completed work order from a filter that excludes it. Arrows
    into a sequence it does not belong to would land somewhere unrelated."""
    a, b, c = three
    body = signed_in.get(f'/work-orders/{c.id}?status=open').get_data(as_text=True)
    assert 'wo-steps' not in body


def test_no_arrows_for_a_list_of_one(signed_in, db):
    only = create_work_order(title='Alone', status='open')
    body = signed_in.get(f'/work-orders/{only.id}').get_data(as_text=True)
    assert 'wo-steps' not in body


def test_back_sits_beside_the_title_not_among_the_actions(signed_in, three):
    """Leaving is a different kind of action from editing or archiving, and it
    belongs where the eye starts rather than at the far right."""
    a, _, _ = three
    body = signed_in.get(f'/work-orders/{a.id}').get_data(as_text=True)

    heading = body.index('class="page-heading"')
    actions = body.index('class="page-actions"')
    # `page-back` is the stable hook; the variant class beside it is styling
    # and has already changed once.
    back = body.index('page-back')
    title = body.index('<h1>')

    assert heading < back < title, 'Back should precede the title inside the heading block'
    assert back < actions, 'Back is still inside the right-hand actions'
