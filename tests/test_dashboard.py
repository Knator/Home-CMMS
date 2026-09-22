"""The four figures across the top of the dashboard.

They answer four different questions — what is on my plate, what is late, what
got done, what is coming — so the set is only useful if each is counting
something the others are not.
"""
from datetime import date, timedelta

import pytest

from app.services import create_work_order
from app.models.pm import PM
from app.extensions import db as _db


def labels(client):
    """The stat labels, in the order they appear."""
    body = client.get('/').get_data(as_text=True)
    out, rest = [], body
    while 'class="stat-label">' in rest:
        rest = rest.split('class="stat-label">', 1)[1]
        out.append(rest.split('<')[0].strip())
    return out


def value_for(client, label):
    body = client.get('/').get_data(as_text=True)
    after = body.split(f'class="stat-label">{label}<', 1)[1]
    return after.split('class="stat-value"', 1)[1].split('>', 1)[1].split('<')[0].strip()


def test_the_cards_read_left_to_right_as_intended(client, db, user, login):
    login()
    assert labels(client) == [
        'Open Work Orders',
        'Overdue Work Orders',
        'Completed in the Last 30 Days',
        'PMs Due in the Next 30 Days',
    ]


def test_the_asset_count_is_gone(client, db, user, login):
    """Removed on purpose: a number that does not change day to day taught
    people to stop reading the row."""
    login()
    assert 'Total Assets' not in client.get('/').get_data(as_text=True)


def test_completed_counts_only_the_last_thirty_days(client, db, user, login):
    today = date.today()
    create_work_order(title='Recent', status='completed',
                      completed_date=today - timedelta(days=5))
    create_work_order(title='Also recent', status='completed',
                      completed_date=today - timedelta(days=29))
    create_work_order(title='Too old', status='completed',
                      completed_date=today - timedelta(days=45))
    create_work_order(title='Still open', status='open')

    login()
    assert value_for(client, 'Completed in the Last 30 Days') == '2'


def test_archiving_does_not_undo_completed_work(client, db, user, login):
    """Archiving is a filing decision. If it reduced this figure, tidying up
    would look like losing work — the rest of the dashboard hides archived
    records from *listings*, which is a different thing."""
    from app.services import archive_work_order

    wo = create_work_order(title='Done and filed', status='completed',
                           completed_date=date.today() - timedelta(days=3))
    login()
    before = value_for(client, 'Completed in the Last 30 Days')
    archive_work_order(wo)
    _db.session.commit()
    assert value_for(client, 'Completed in the Last 30 Days') == before


def test_cancelled_work_is_not_counted_as_completed(client, db, user, login):
    create_work_order(title='Called off', status='cancelled',
                      completed_date=date.today() - timedelta(days=2))
    login()
    assert value_for(client, 'Completed in the Last 30 Days') == '0'


def test_on_hold_work_counts_as_open(client, db, user, login):
    """A parked work order is unfinished, and it still blocks its PM from
    generating another — so leaving it out of this figure made it invisible
    here while it went on having an effect elsewhere."""
    create_work_order(title='Waiting on a part', status='on_hold')
    create_work_order(title='Being worked', status='in_progress')
    create_work_order(title='Untouched', status='open')
    create_work_order(title='Finished', status='completed',
                      completed_date=date.today())

    login()
    assert value_for(client, 'Open Work Orders') == '3'


def test_on_hold_is_not_reported_as_overdue(client, db, user, login):
    """Deliberately paused work is late in the calendar sense but not in the
    sense the red figure is asking about. Stated here because the two counts
    now use different status sets on purpose."""
    create_work_order(title='Parked and late', status='on_hold',
                      due_date=date.today() - timedelta(days=60),
                      overdue_grace_days=0)
    login()
    assert value_for(client, 'Open Work Orders') == '1'
    assert value_for(client, 'Overdue Work Orders') == '0'
