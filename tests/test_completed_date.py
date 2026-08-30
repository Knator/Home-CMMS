"""Manual completion dates, defaulting to today when left blank."""
from datetime import date, timedelta

from app.models.work_order import WorkOrder
from app.services import create_work_order
from tests.conftest import CSRF


def test_blank_on_completion_uses_today(client, db, user, login):
    login()
    client.post('/work-orders/new', data={
        'title': 'Done today', 'status': 'completed', 'completed_date': '',
        'csrf_token': CSRF})
    assert WorkOrder.query.one().completed_date == date.today()


def test_a_manual_date_is_kept(client, db, user, login):
    login()
    client.post('/work-orders/new', data={
        'title': 'Backdated', 'status': 'completed', 'completed_date': '2026-03-01',
        'csrf_token': CSRF})
    assert WorkOrder.query.one().completed_date == date(2026, 3, 1)


def test_a_date_can_be_set_without_completing(client, db, user, login):
    """Recording when work happened shouldn't force the status."""
    login()
    client.post('/work-orders/new', data={
        'title': 'Logged later', 'status': 'open', 'completed_date': '2026-02-14',
        'csrf_token': CSRF})
    wo = WorkOrder.query.one()
    assert wo.completed_date == date(2026, 2, 14)
    assert wo.status == 'open'


def test_open_work_orders_have_no_date_by_default(client, db, user, login):
    login()
    client.post('/work-orders/new', data={
        'title': 'Still open', 'status': 'open', 'csrf_token': CSRF})
    assert WorkOrder.query.one().completed_date is None


def test_editing_can_change_the_date(client, db, user, login):
    login()
    wo = create_work_order(title='Job', status='completed', completed_date=date(2026, 1, 1))
    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': 'Job', 'status': 'completed', 'completed_date': '2026-05-20',
        'csrf_token': CSRF})
    assert wo.completed_date == date(2026, 5, 20)


def test_clearing_the_field_on_a_completed_wo_falls_back_to_today(client, db, user, login):
    login()
    wo = create_work_order(title='Job', status='completed', completed_date=date(2026, 1, 1))
    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': 'Job', 'status': 'completed', 'completed_date': '', 'csrf_token': CSRF})
    assert wo.completed_date == date.today()


def test_clearing_the_field_on_an_open_wo_clears_it(client, db, user, login):
    login()
    wo = create_work_order(title='Job', status='completed', completed_date=date(2026, 1, 1))
    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': 'Job', 'status': 'open', 'completed_date': '', 'csrf_token': CSRF})
    assert wo.completed_date is None


def test_a_post_without_the_field_leaves_the_date_alone(client, db, user, login):
    """An absent input must not wipe history; only an empty one clears."""
    login()
    wo = create_work_order(title='Job', status='completed', completed_date=date(2026, 1, 1))
    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': 'Job', 'status': 'in_progress', 'csrf_token': CSRF})
    assert wo.completed_date == date(2026, 1, 1)


def test_a_malformed_date_on_a_completed_wo_falls_back_to_today(client, db, user, login):
    login()
    client.post('/work-orders/new', data={
        'title': 'Junk date', 'status': 'completed', 'completed_date': '31/02/2026',
        'csrf_token': CSRF})
    assert WorkOrder.query.one().completed_date == date.today()


def test_the_form_prefills_the_existing_date(client, db, user, login):
    login()
    wo = create_work_order(title='Job', status='completed', completed_date=date(2026, 4, 9))
    body = client.get(f'/work-orders/{wo.id}/edit').get_data(as_text=True)
    assert 'name="completed_date"' in body
    assert 'value="2026-04-09"' in body


def test_completed_date_appears_in_the_lists(client, db, user, login):
    login()
    create_work_order(title='Shown', status='completed', completed_date=date(2026, 4, 9))
    for path in ('/work-orders/', '/'):
        assert '2026-04-09' in client.get(path).get_data(as_text=True), path
