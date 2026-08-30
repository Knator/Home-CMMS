"""Job plan task replacement and cascade behaviour."""
from app.models.job_plan import JobPlan, JobPlanTask
from tests.conftest import CSRF


def create_plan(client, name='Flush Water Heater'):
    return client.post('/job-plans/new', data={
        'name': name,
        'task_count': '3',
        'task_0_description': 'Turn off power',
        'task_1_description': 'Drain tank',
        'task_2_description': 'Refill',
        'task_2_minutes': '15',
        'csrf_token': CSRF,
    })


def test_tasks_are_saved_in_order(client, db, user, login):
    login()
    create_plan(client)

    plan = JobPlan.query.one()
    tasks = plan.tasks.all()
    assert [t.description for t in tasks] == ['Turn off power', 'Drain tank', 'Refill']
    assert [t.sequence for t in tasks] == [1, 2, 3]
    assert tasks[2].estimated_minutes == 15


def test_blank_rows_do_not_leave_sequence_gaps(client, db, user, login):
    login()
    client.post('/job-plans/new', data={
        'name': 'Sparse', 'task_count': '3',
        'task_0_description': 'First',
        'task_1_description': '   ',
        'task_2_description': 'Third',
        'csrf_token': CSRF,
    })
    plan = JobPlan.query.one()
    assert [t.sequence for t in plan.tasks.all()] == [1, 2]


def test_editing_replaces_the_task_list(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()

    client.post(f'/job-plans/{plan.id}/edit', data={
        'name': 'Flush Water Heater', 'task_count': '1',
        'task_0_description': 'Single replacement step', 'csrf_token': CSRF,
    })

    assert plan.tasks.count() == 1
    assert JobPlanTask.query.count() == 1
    assert plan.tasks.one().description == 'Single replacement step'


def test_deleting_a_plan_deletes_its_tasks(client, db, user, login):
    login()
    create_plan(client)
    plan = JobPlan.query.one()

    client.post(f'/job-plans/{plan.id}/delete', data={'csrf_token': CSRF})

    assert JobPlan.query.count() == 0
    assert JobPlanTask.query.count() == 0


def test_deleting_a_plan_leaves_its_work_orders(client, db, user, login):
    from app.services import create_work_order
    from app.models.work_order import WorkOrder

    login()
    create_plan(client)
    plan = JobPlan.query.one()
    create_work_order(title='Uses the plan', job_plan_id=plan.id)

    client.post(f'/job-plans/{plan.id}/delete', data={'csrf_token': CSRF})

    wo = WorkOrder.query.one()
    assert wo.job_plan_id is None
