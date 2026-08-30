"""Columns on the work order list, dashboard and location detail."""
from datetime import date

from app.models.location import Location
from app.services import create_asset, create_work_order
from tests.conftest import CSRF


def test_work_order_list_shows_location_and_completed(client, db, user, login):
    loc = Location(name='Garage')
    db.session.add(loc)
    db.session.commit()
    create_work_order(title='Fix door', location_id=loc.id, status='completed',
                      completed_date=date(2026, 4, 9))

    login()
    body = client.get('/work-orders/').get_data(as_text=True)
    assert '<th>Location</th>' in body
    assert '<th>Completed</th>' in body
    assert 'Garage' in body
    assert '2026-04-09' in body


def test_dashboard_drops_the_wo_number_and_shows_completed(client, db, user, login):
    create_work_order(title='Recent job', status='completed', completed_date=date(2026, 4, 9))

    login()
    body = client.get('/').get_data(as_text=True)
    assert '<th>WO #</th>' not in body
    assert '<th>Completed</th>' in body
    assert 'Recent job' in body        # the title is the link now
    assert '2026-04-09' in body


def test_location_detail_lists_its_work_orders(client, db, user, login):
    loc = Location(name='Garage')
    other = Location(name='Attic')
    db.session.add_all([loc, other])
    db.session.commit()
    create_work_order(title='Garage job', location_id=loc.id)
    create_work_order(title='Attic job', location_id=other.id)

    login()
    body = client.get(f'/locations/{loc.id}').get_data(as_text=True)
    assert 'Work Orders' in body
    assert 'Garage job' in body
    assert 'Attic job' not in body


def test_location_with_no_work_orders_says_so(client, db, user, login):
    loc = Location(name='Empty')
    db.session.add(loc)
    db.session.commit()

    login()
    assert 'No work orders for this location' in client.get(f'/locations/{loc.id}').get_data(as_text=True)


def test_location_work_orders_show_the_asset(client, db, user, login):
    loc = Location(name='Garage')
    db.session.add(loc)
    db.session.commit()
    asset = create_asset(name='Door Opener', location_id=loc.id)
    create_work_order(title='Fix door', location_id=loc.id, asset_id=asset.id)

    login()
    body = client.get(f'/locations/{loc.id}').get_data(as_text=True)
    assert 'Door Opener' in body


def test_searchable_selects_are_marked_up(client, db, user, login):
    """The filter widget is progressive enhancement over these selects."""
    login()
    body = client.get('/work-orders/new').get_data(as_text=True)
    assert body.count('data-searchable') >= 3
    assert 'js/main.js' in body


def test_pm_form_inherits_the_location_from_the_asset(client, db, user, login):
    """Same wiring as the work order form: pick an asset, get its location."""
    login()
    body = client.get('/pms/new').get_data(as_text=True)
    assert 'data-summary-url="/assets/0/summary"' in body
    assert 'id="asset_id"' in body
    assert 'id="location_id"' in body
    assert 'id="location-hint"' in body


def test_pm_edit_form_has_the_same_wiring(client, db, user, login):
    from datetime import date
    from app.models.pm import PM

    pm = PM(name='Annual', interval_days=365, next_due_date=date.today())
    db.session.add(pm)
    db.session.commit()

    login()
    body = client.get(f'/pms/{pm.id}/edit').get_data(as_text=True)
    assert 'data-summary-url="/assets/0/summary"' in body
    assert 'id="location-hint"' in body
