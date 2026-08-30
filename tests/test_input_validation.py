"""Untrusted query-string and form values must not raise 500s."""
from tests.conftest import CSRF


def test_asset_filter_with_non_numeric_location_id(client, user, login):
    """/assets/?location_id=abc used to raise ValueError from a bare int()."""
    login()
    response = client.get('/assets/?location_id=abc')
    assert response.status_code == 200


def test_asset_filter_with_valid_location_id(client, db, user, login):
    from app.models.location import Location
    from app.models.asset import Asset

    garage = Location(name='Garage')
    db.session.add(garage)
    db.session.commit()
    db.session.add(Asset(name='Water Heater', location_id=garage.id))
    db.session.add(Asset(name='Mower'))
    db.session.commit()

    login()
    response = client.get(f'/assets/?location_id={garage.id}')
    body = response.get_data(as_text=True)
    assert 'Water Heater' in body
    assert 'Mower' not in body


def test_job_plan_with_non_numeric_task_count(client, user, login):
    """task_count is a hidden field, so it is untrusted input."""
    login()
    response = client.post('/job-plans/new', data={
        'name': 'Bad count', 'task_count': 'not-a-number', 'csrf_token': CSRF,
    })
    assert response.status_code == 302

    from app.models.job_plan import JobPlan
    assert JobPlan.query.filter_by(name='Bad count').first() is not None


def test_job_plan_task_count_is_capped(client, user, login):
    login()
    response = client.post('/job-plans/new', data={
        'name': 'Huge', 'task_count': '10000000',
        'task_0_description': 'Only real task', 'csrf_token': CSRF,
    })
    assert response.status_code == 302

    from app.models.job_plan import JobPlan
    plan = JobPlan.query.filter_by(name='Huge').first()
    assert plan.tasks.count() == 1


def test_work_order_rejects_forged_status(client, user, login):
    login()
    client.post('/work-orders/new', data={
        'title': 'Forged', 'status': 'pwned', 'priority': 'nonsense',
        'wo_type': 'bogus', 'csrf_token': CSRF,
    })
    from app.models.work_order import WorkOrder
    wo = WorkOrder.query.filter_by(title='Forged').first()
    assert wo.status == 'open'
    assert wo.priority == 'medium'
    assert wo.wo_type == 'unplanned'


def test_malformed_dates_are_ignored_not_fatal(client, user, login):
    login()
    response = client.post('/work-orders/new', data={
        'title': 'Bad date', 'due_date': '31/02/2026', 'csrf_token': CSRF,
    })
    assert response.status_code == 302

    from app.models.work_order import WorkOrder
    assert WorkOrder.query.filter_by(title='Bad date').first().due_date is None
