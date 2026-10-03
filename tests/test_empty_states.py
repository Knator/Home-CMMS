"""An empty list says *why* it is empty.

"No job plans yet. Create one." appeared when 220 existed and a search matched
none of them — which invites someone to create a duplicate of the thing they
were just looking at.
"""
import re

import pytest

from app.extensions import db as _db
from app.models.job_plan import JobPlan
from app.models.pm import PM
from app.services import create_asset, create_location, create_work_order
from tests.conftest import make_user

from datetime import date

LISTS = [
    ('/work-orders/', 'work orders'),
    ('/pms/', 'PM schedules'),
    ('/job-plans/', 'job plans'),
    ('/assets/', 'assets'),
    ('/locations/', 'locations'),
]


@pytest.fixture
def signed_in(client, db, login):
    make_user('tester', role='admin', password='Password123!')
    login('tester', 'Password123!')
    return client


@pytest.fixture
def one_of_each(db):
    loc = create_location(name='Shed')
    create_asset(name='Mower', location_id=loc.id)
    _db.session.add(JobPlan(name='Service the mower'))
    _db.session.add(PM(name='Mower service', interval_days=30,
                       next_due_date=date.today()))
    _db.session.commit()
    create_work_order(title='Sharpen the blade', status='open')


def message(client, url):
    body = client.get(url).get_data(as_text=True)
    found = re.search(r'card-body text-muted">\s*(.*?)\s*</div>', body, re.S)
    return ' '.join(found.group(1).split()) if found else ''


@pytest.mark.parametrize('url,noun', LISTS)
def test_an_empty_instance_invites_you_to_create(signed_in, url, noun):
    text = message(signed_in, url)
    assert f'No {noun} yet.' in text, text
    assert 'Create one.' in text, text
    assert 'match the current filters' not in text, text


@pytest.mark.parametrize('url,noun', LISTS)
def test_a_search_that_matches_nothing_says_so(signed_in, one_of_each, url, noun):
    """The reported bug. Records exist; the search simply found none of them."""
    text = message(signed_in, f'{url}?q=zzzznothingmatches')
    assert f'No {noun} match the current filters.' in text, text
    assert 'Create one' not in text, 'still inviting a duplicate'


@pytest.mark.parametrize('url,noun', LISTS)
def test_the_offer_to_clear_goes_back_to_the_unfiltered_list(signed_in, one_of_each, url, noun):
    body = signed_in.get(f'{url}?q=zzzznothingmatches').get_data(as_text=True)
    empty = body[body.index('card-body text-muted'):]
    empty = empty[:empty.index('</div>')]
    href = empty.split('href="')[1].split('"')[0]
    assert href.rstrip('/') == url.rstrip('/'), href
    assert '?' not in href, 'the clear link kept a filter'


def test_a_default_filter_counts_as_filtered(signed_in, db):
    """Every list hides something by default — inactive assets here — so a list
    can be empty with no query string at all and still not be empty underneath.
    That is why this checks the table rather than looking for a search box."""
    loc = create_location(name='Shed')
    create_asset(name='Retired mower', location_id=loc.id, status='decommissioned')

    text = message(signed_in, '/assets/')
    assert 'match the current filters' in text, text
    assert 'No assets yet' not in text


def test_an_empty_table_still_says_yet_even_with_a_search(signed_in, db):
    """Nothing to find and nothing to clear: offering to clear the filters
    would send someone to an equally empty list."""
    text = message(signed_in, '/job-plans/?q=anything')
    assert 'No job plans yet.' in text, text
