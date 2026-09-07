"""Every form that can hold a lot of typing warns before it is abandoned.

The JS opts in via `data-warn-unsaved` on the form, so a template that loses the
attribute silently stops protecting the page. These check the rendered pages
rather than the template text, because the edit and create routes render the
same template and both need it.
"""
from datetime import date

import pytest

from app.services import create_asset, create_location, create_work_order
from tests.conftest import prime_csrf


@pytest.fixture
def signed_in(client, db, user, login):
    login()
    prime_csrf(client)
    return client


@pytest.fixture
def records(db):
    from app.models.job_plan import JobPlan
    from app.models.pm import PM
    from app.extensions import db as _db

    location = create_location(name='Utility Room')
    asset = create_asset(name='Furnace', location_id=location.id)
    wo = create_work_order(title='Replace filter', wo_type='planned')
    plan = JobPlan(name='Annual service')
    _db.session.add(plan)
    _db.session.commit()
    pm = PM(name='Filter change', interval_days=90, job_plan_id=plan.id,
            next_due_date=date.today())
    _db.session.add(pm)
    _db.session.commit()
    return {'location': location, 'asset': asset, 'wo': wo, 'plan': plan, 'pm': pm}


CREATE_PAGES = [
    '/work-orders/new',
    '/assets/new',
    '/locations/new',
    '/pms/new',
    '/job-plans/new',
]


@pytest.mark.parametrize('url', CREATE_PAGES)
def test_create_forms_warn_before_being_abandoned(signed_in, url):
    html = signed_in.get(url).get_data(as_text=True)
    assert 'data-warn-unsaved' in html


def test_edit_forms_warn_too(signed_in, records):
    """An edit form holds more typing than a create form, not less."""
    urls = [
        f"/work-orders/{records['wo'].id}/edit",
        f"/assets/{records['asset'].id}/edit",
        f"/locations/{records['location'].id}/edit",
        f"/pms/{records['pm'].id}/edit",
        f"/job-plans/{records['plan'].id}/edit",
    ]
    for url in urls:
        response = signed_in.get(url)
        assert response.status_code == 200, url
        assert 'data-warn-unsaved' in response.get_data(as_text=True), url


def test_the_attribute_is_on_the_form_itself(signed_in):
    """initUnsavedWarning() looks for form[data-warn-unsaved]; on any other
    element it would find nothing and protect nothing."""
    import re
    html = signed_in.get('/work-orders/new').get_data(as_text=True)
    form = re.search(r'<form[^>]*>', html).group(0)
    assert 'data-warn-unsaved' in form


def test_the_create_modal_page_is_protected_as_well(signed_in):
    """The embedded create form is where the iframe-close check applies."""
    html = signed_in.get('/assets/new?embedded=1').get_data(as_text=True)
    assert 'data-warn-unsaved' in html


def test_pages_without_a_form_do_not_arm_it(signed_in, records):
    """A detail page has nothing to lose; warning there would be noise."""
    for url in ('/', f"/assets/{records['asset'].id}", '/assets/'):
        assert 'data-warn-unsaved' not in signed_in.get(url).get_data(as_text=True)
