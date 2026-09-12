"""Searching job plans.

Same box as the work order list, but the text it searches includes the plan's
*task descriptions*, which live in another table.
"""
import pytest

from app.extensions import db as _db
from app.models.job_plan import JobPlan, JobPlanTask
from tests.conftest import prime_csrf


@pytest.fixture
def signed_in(client, db, user, login):
    login()
    prime_csrf(client)
    return client


@pytest.fixture
def plans(db):
    made = {}
    for name, description, notes, tasks in [
        ('Annual Furnace Service', 'Yearly check', None,
         ['Replace the air filter', 'Vacuum the burners']),
        ('Gutter Clearing', None, 'Bring the tall ladder', ['Clear downspouts']),
        ('Water Heater Flush', 'Drain sediment', None,
         ['Attach hose', 'Open drain valve']),
    ]:
        plan = JobPlan(name=name, description=description, notes=notes)
        _db.session.add(plan)
        _db.session.commit()
        for i, task in enumerate(tasks, 1):
            _db.session.add(JobPlanTask(job_plan_id=plan.id, sequence=i,
                                        description=task))
        _db.session.commit()
        made[name] = plan
    return made


def shown(client, plans, query=''):
    html = client.get('/job-plans/' + query).get_data(as_text=True)
    return {name for name in plans if name in html}


# ── the four places it looks ───────────────────────────────────────────────

def test_it_searches_the_name(signed_in, plans):
    assert shown(signed_in, plans, '?q=furnace') == {'Annual Furnace Service'}


def test_it_searches_the_description(signed_in, plans):
    assert shown(signed_in, plans, '?q=sediment') == {'Water Heater Flush'}


def test_it_searches_the_notes(signed_in, plans):
    assert shown(signed_in, plans, '?q=ladder') == {'Gutter Clearing'}


def test_it_searches_task_descriptions(signed_in, plans):
    """The one that needs another table."""
    assert shown(signed_in, plans, '?q=downspouts') == {'Gutter Clearing'}
    assert shown(signed_in, plans, '?q=air%20filter') == {'Annual Furnace Service'}


def test_a_plan_with_several_matching_tasks_appears_once(signed_in, db):
    """Reached with EXISTS rather than a join, which would repeat the row."""
    plan = JobPlan(name='Repeated')
    _db.session.add(plan)
    _db.session.commit()
    for i in range(3):
        _db.session.add(JobPlanTask(job_plan_id=plan.id, sequence=i + 1,
                                    description=f'check widget {i}'))
    _db.session.commit()

    html = signed_in.get('/job-plans/?q=widget').get_data(as_text=True)
    assert html.count('>Repeated<') == 1


# ── how it behaves ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('needle', ['DOWNSPOUTS', 'downspouts', 'DownSpouts'])
def test_search_is_case_insensitive(signed_in, plans, needle):
    assert shown(signed_in, plans, f'?q={needle}') == {'Gutter Clearing'}


def test_an_empty_search_lists_everything(signed_in, plans):
    assert shown(signed_in, plans) == set(plans)
    assert shown(signed_in, plans, '?q=') == set(plans)


def test_no_hits_lists_nothing(signed_in, plans):
    assert shown(signed_in, plans, '?q=zzzznotfound') == set()


def test_a_percent_sign_is_searched_for_literally(signed_in, db, plans):
    plan = JobPlan(name='Coolant 50% mix')
    _db.session.add(plan)
    _db.session.commit()
    html = signed_in.get('/job-plans/?q=50%25').get_data(as_text=True)
    assert 'Coolant 50% mix' in html
    assert 'Gutter Clearing' not in html


# ── the regex toggle works here too ────────────────────────────────────────

def test_regex_matches_across_the_same_fields(signed_in, plans):
    assert shown(signed_in, plans, '?regex=1&q=furnace%7Cgutter') == {
        'Annual Furnace Service', 'Gutter Clearing'}


def test_regex_reaches_task_descriptions(signed_in, plans):
    assert shown(signed_in, plans, '?regex=1&q=down.pouts') == {'Gutter Clearing'}


def test_regex_is_off_unless_asked_for(signed_in, plans):
    assert shown(signed_in, plans, '?q=furnace%7Cgutter') == set()


def test_an_invalid_pattern_is_reported(signed_in, plans):
    response = signed_in.get('/job-plans/?regex=1&q=%5B')
    assert response.status_code == 200
    assert b'not a valid regular expression' in response.data


def test_a_pathological_pattern_is_stopped(signed_in, db, monkeypatch):
    from app import search
    monkeypatch.setattr(search, 'REGEX_TIME_BUDGET', 0.3)
    for i in range(30):
        plan = JobPlan(name=f'Plan {i}', notes='a' * 60 + ' tail')
        _db.session.add(plan)
    _db.session.commit()

    response = signed_in.get('/job-plans/?regex=1&q=%28%3F%3Aa%7Caa%29%2B%24')
    assert response.status_code == 200
    assert b'took longer than' in response.data


# ── shared with the work order list ────────────────────────────────────────

def test_both_lists_use_the_same_search_box(signed_in, plans):
    """One macro, so the two cannot drift apart."""
    job_plans = signed_in.get('/job-plans/').get_data(as_text=True)
    work_orders = signed_in.get('/work-orders/').get_data(as_text=True)
    for markup in ('class="filter-search"', 'class="regex-check"',
                   'for="regex-toggle"'):
        assert markup in job_plans, markup
        assert markup in work_orders, markup


def test_the_placeholder_says_what_this_list_searches(signed_in, plans):
    html = signed_in.get('/job-plans/').get_data(as_text=True)
    assert 'tasks' in html
