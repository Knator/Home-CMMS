"""Work order numbering and status lifecycle."""
from datetime import date

from app.models.work_order import WorkOrder
from app.services import create_work_order
from tests.conftest import CSRF


def test_numbers_are_sequential_within_the_year(app, db):
    year = date.today().year
    first = create_work_order(title='One')
    second = create_work_order(title='Two')

    assert first.wo_number == f'WO-{year}-00001'
    assert second.wo_number == f'WO-{year}-00002'


def test_duplicate_number_is_retried_not_raised(app, db, monkeypatch):
    """A concurrent insert can steal the number; the unique constraint must not 500."""
    create_work_order(title='Existing')

    year = date.today().year
    taken = [f'WO-{year}-00001', f'WO-{year}-00003']

    def flaky_number():
        return taken.pop(0) if taken else f'WO-{year}-00099'

    monkeypatch.setattr(WorkOrder, 'generate_wo_number', staticmethod(flaky_number))
    wo = create_work_order(title='Racer')
    assert wo.wo_number == f'WO-{year}-00003'
    assert WorkOrder.query.count() == 2


def test_completion_timestamp_survives_reopening(client, db, user, login):
    """Reopening a work order must not erase when it was finished."""
    login()
    client.post('/work-orders/new', data={
        'title': 'Replace filter', 'status': 'open', 'csrf_token': CSRF,
    })
    wo = WorkOrder.query.filter_by(title='Replace filter').one()
    assert wo.completed_date is None

    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': 'Replace filter', 'status': 'completed', 'csrf_token': CSRF,
    })
    completed_at = wo.completed_date
    assert completed_at is not None

    client.post(f'/work-orders/{wo.id}/edit', data={
        'title': 'Replace filter', 'status': 'in_progress', 'csrf_token': CSRF,
    })
    assert wo.status == 'in_progress'
    assert wo.completed_date == completed_at


def test_created_as_completed_gets_a_timestamp(client, db, user, login):
    login()
    client.post('/work-orders/new', data={
        'title': 'Already done', 'status': 'completed', 'csrf_token': CSRF,
    })
    assert WorkOrder.query.filter_by(title='Already done').one().completed_date is not None


def test_list_filters_ignore_unknown_values(client, db, user, login):
    login()
    create_work_order(title='Visible', status='open')
    response = client.get('/work-orders/?status=bogus')
    assert response.status_code == 200
    assert 'Visible' in response.get_data(as_text=True)


def test_overdue_flag(app, db):
    from datetime import timedelta
    wo = create_work_order(title='Late', due_date=date.today() - timedelta(days=1))
    assert wo.is_overdue

    wo.status = 'completed'
    assert not wo.is_overdue
