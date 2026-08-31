"""The priority a PM stamps on the work orders it generates."""
from datetime import date

from app.models.pm import PM
from app.models.work_order import WorkOrder
from app.scheduler import run_pm_check
from app.services import generate_work_order_for_pm
from tests.conftest import CSRF


def make_pm(db, priority='medium'):
    pm = PM(name='Filter change', interval_days=30, next_due_date=date.today(),
            wo_priority=priority)
    db.session.add(pm)
    db.session.commit()
    return pm


def test_defaults_to_medium(db):
    pm = PM(name='Plain', interval_days=30, next_due_date=date.today())
    db.session.add(pm)
    db.session.commit()
    assert pm.wo_priority == 'medium'


def test_generated_work_order_takes_the_pms_priority(db):
    pm = make_pm(db, 'high')
    assert generate_work_order_for_pm(pm).priority == 'high'


def test_the_scheduler_uses_it_too(app, db):
    pm = make_pm(db, 'critical')
    run_pm_check(app)
    assert WorkOrder.query.filter_by(pm_id=pm.id).one().priority == 'critical'


def test_manual_generation_uses_it(client, db, user, login):
    pm = make_pm(db, 'low')
    login()
    client.post(f'/pms/{pm.id}/generate', data={'csrf_token': CSRF})
    assert WorkOrder.query.filter_by(pm_id=pm.id).one().priority == 'low'


def test_priority_round_trips_through_the_form(client, db, user, login):
    login()
    client.post('/pms/new', data={
        'name': 'Urgent PM', 'interval_days': '30', 'wo_priority': 'high',
        'next_due_date': date.today().isoformat(), 'csrf_token': CSRF})
    assert PM.query.one().wo_priority == 'high'


def test_a_forged_priority_falls_back_to_medium(client, db, user, login):
    login()
    client.post('/pms/new', data={
        'name': 'Forged', 'interval_days': '30', 'wo_priority': 'catastrophic',
        'next_due_date': date.today().isoformat(), 'csrf_token': CSRF})
    assert PM.query.one().wo_priority == 'medium'


def test_priority_can_be_changed(client, db, user, login):
    pm = make_pm(db, 'low')
    login()
    client.post(f'/pms/{pm.id}/edit', data={
        'name': pm.name, 'interval_days': '30', 'wo_priority': 'critical',
        'next_due_date': pm.next_due_date.isoformat(), 'is_active': '1',
        'csrf_token': CSRF})
    assert pm.wo_priority == 'critical'


def test_form_and_detail_show_the_priority(client, db, user, login):
    pm = make_pm(db, 'high')
    login()
    assert 'Work Order Priority' in client.get('/pms/new').get_data(as_text=True)
    assert 'Work Order Priority' in client.get(f'/pms/{pm.id}').get_data(as_text=True)
