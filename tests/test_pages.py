"""Every page renders for a signed-in user."""
import pytest

from tests.conftest import CSRF


@pytest.fixture
def seeded(db, admin):
    from app.models.location import Location
    from app.models.asset import Asset
    from app.models.job_plan import JobPlan, JobPlanTask
    from app.models.pm import PM
    from app.services import create_location, create_asset, create_work_order
    from datetime import date

    loc = create_location(name='Garage')
    db.session.add(loc)
    db.session.flush()

    asset = create_asset(name='Water Heater', location_id=loc.id, category='Plumbing')
    plan = JobPlan(name='Flush', created_by=admin.id)
    db.session.add(plan)
    db.session.flush()

    db.session.add(JobPlanTask(job_plan_id=plan.id, sequence=1, description='Drain'))
    pm = PM(name='Annual flush', asset_id=asset.id, job_plan_id=plan.id,
            interval_days=365, next_due_date=date.today())
    db.session.add(pm)
    db.session.commit()

    wo = create_work_order(title='Fix drip', asset_id=asset.id, location_id=loc.id,
                           job_plan_id=plan.id, created_by=admin.id)
    return dict(location=loc, asset=asset, plan=plan, pm=pm, wo=wo, admin=admin)


def test_all_pages_render(client, seeded, login):
    login('admin')
    ids = {k: v.id for k, v in seeded.items()}

    paths = [
        '/', '/locations/', '/locations/new', f"/locations/{ids['location']}",
        f"/locations/{ids['location']}/edit",
        '/assets/', '/assets/new', f"/assets/{ids['asset']}", f"/assets/{ids['asset']}/edit",
        '/work-orders/', '/work-orders/new', f"/work-orders/{ids['wo']}",
        f"/work-orders/{ids['wo']}/edit",
        '/job-plans/', '/job-plans/new', f"/job-plans/{ids['plan']}",
        f"/job-plans/{ids['plan']}/edit",
        '/pms/', '/pms/new', f"/pms/{ids['pm']}", f"/pms/{ids['pm']}/edit", '/pms/?show=all',
        '/admin/users', '/admin/users/new', f"/admin/users/{ids['admin']}/edit",
        '/auth/change-password',
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, f'{path} returned {response.status_code}'


def test_dashboard_counts(client, seeded, login):
    login('admin')
    body = client.get('/').get_data(as_text=True)
    assert 'Fix drip' in body
    assert 'Annual flush' in body


def test_change_password_flow(client, db, user, login):
    login()
    client.post('/auth/change-password', data={
        'current_password': 'password123',
        'new_password': 'brand-new-password',
        'confirm_password': 'brand-new-password',
        'csrf_token': CSRF,
    })
    assert user.check_password('brand-new-password')


def test_change_password_requires_the_current_one(client, db, user, login):
    login()
    client.post('/auth/change-password', data={
        'current_password': 'wrong',
        'new_password': 'brand-new-password',
        'confirm_password': 'brand-new-password',
        'csrf_token': CSRF,
    })
    assert user.check_password('password123')


def test_login_page_is_self_contained(client):
    """The login page renders outside base.html, so it needs its own theme wiring."""
    body = client.get('/auth/login').get_data(as_text=True)
    assert 'js/theme.js' in body
    assert 'theme-toggle' in body
    assert 'name="viewport"' in body


def test_signed_in_pages_carry_the_theme_toggle(client, seeded, login):
    login('admin')
    body = client.get('/').get_data(as_text=True)
    assert 'js/theme.js' in body
    assert 'theme-toggle' in body
