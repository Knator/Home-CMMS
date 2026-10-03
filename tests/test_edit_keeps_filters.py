"""Editing a record and coming back keeps the list's filters.

The trail broke at the edit form: none of the links into it carried the
filters, the save redirected to the record without them, and Cancel went back
to an unfiltered list. Each hop is checked separately below, then the whole
round trip, because a fix that covers four hops and misses the fifth still
lands on the wrong list.
"""
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db as _db
from app.models.job_plan import JobPlan
from app.models.pm import PM
from app.services import create_asset, create_location, create_work_order
from tests.conftest import CSRF, make_user

from datetime import date

ENTITIES = ['work_orders', 'pms', 'job_plans', 'assets', 'locations']
PREFIX = {'work_orders': '/work-orders', 'pms': '/pms', 'job_plans': '/job-plans',
          'assets': '/assets', 'locations': '/locations'}
FILTER = 'Zebra'


@pytest.fixture
def signed_in(client, db, login):
    make_user('tester', role='admin', password='Password123!')
    login('tester', 'Password123!')
    with client.session_transaction() as session:
        session['csrf_token'] = CSRF
    return client


@pytest.fixture
def records(db):
    loc = create_location(name=f'{FILTER} shed')
    asset = create_asset(name=f'{FILTER} mower', location_id=loc.id)
    plan = JobPlan(name=f'{FILTER} service')
    pm = PM(name=f'{FILTER} PM', interval_days=30, next_due_date=date.today())
    _db.session.add_all([plan, pm])
    _db.session.commit()
    wo = create_work_order(title=f'{FILTER} job', status='open')
    # `loc` holds an asset, so it cannot be deleted and its page shows no delete
    # form; this one is empty, for the paths that need a delete to go through.
    empty = create_location(name=f'{FILTER} empty room')
    return {'work_orders': wo, 'pms': pm, 'job_plans': plan, 'assets': asset,
            'locations': loc, 'empty_location': empty}


def deletable(records, entity):
    return records['empty_location'] if entity == 'locations' else records[entity]


def carries_filter(url):
    return parse_qs(urlsplit(url).query).get('q') == [FILTER]


def hrefs(body):
    return [h for h in re.findall(r'href="([^"]+)"', body)]


class FormFields(HTMLParser):
    """What a browser would submit for the first POST form on a page: every
    named input with a value, checked boxes only, each select's chosen option,
    and textareas."""

    def __init__(self):
        super().__init__()
        self.data, self._in_form, self._done = [], False, False
        self._select = self._textarea = None
        self._chosen = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self._done:
            return
        if tag == 'form' and a.get('method', '').lower() == 'post':
            self._in_form = True
        if not self._in_form:
            return
        name = a.get('name')
        if tag == 'input' and name:
            kind = a.get('type', 'text')
            if kind in ('submit', 'button', 'file'):
                return
            if kind in ('checkbox', 'radio') and 'checked' not in a:
                return
            self.data.append((name, a.get('value', 'on' if kind == 'checkbox' else '')))
        elif tag == 'select' and name:
            self._select = name
            self._chosen[name] = None
        elif tag == 'option' and self._select:
            if 'selected' in a or self._chosen[self._select] is None:
                if 'selected' in a or self._chosen.get(self._select + '#first') is None:
                    self._chosen[self._select] = a.get('value', '')
                    self._chosen[self._select + '#first'] = True
        elif tag == 'textarea' and name:
            self._textarea = [name, '']

    def handle_data(self, data):
        if self._textarea is not None:
            self._textarea[1] += data

    def handle_endtag(self, tag):
        if tag == 'select' and self._select:
            self.data.append((self._select, self._chosen[self._select] or ''))
            self._select = None
        elif tag == 'textarea' and self._textarea:
            self.data.append(tuple(self._textarea))
            self._textarea = None
        elif tag == 'form' and self._in_form:
            self._in_form, self._done = False, True


def form_payload(body):
    """A MultiDict, as a browser would send: repeatable rows reuse names."""
    parser = FormFields()
    parser.feed(body)
    return MultiDict(parser.data)


# ── each hop ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('entity', ENTITIES)
def test_the_list_s_edit_buttons_carry_the_filters(signed_in, records, entity):
    body = signed_in.get(f'{PREFIX[entity]}/?q={FILTER}&show=all').get_data(as_text=True)
    edits = [h for h in hrefs(body) if h.split('?')[0].endswith('/edit')]
    assert edits, f'no edit links on the {entity} list'
    assert all(carries_filter(h) for h in edits), edits


@pytest.mark.parametrize('entity', ENTITIES)
def test_the_record_s_edit_button_carries_the_filters(signed_in, records, entity):
    record = records[entity]
    body = signed_in.get(f'{PREFIX[entity]}/{record.id}?q={FILTER}').get_data(as_text=True)
    edits = [h for h in hrefs(body) if h.split('?')[0].endswith(f'/{record.id}/edit')]
    assert edits, f'no edit link on the {entity} record'
    assert all(carries_filter(h) for h in edits), edits


@pytest.mark.parametrize('entity', ENTITIES)
def test_saving_returns_to_the_record_with_the_filters(signed_in, records, entity):
    """The edit form posts to its own URL, query string included, so the
    filters arrive with the save and only the redirect has to pass them on."""
    record = records[entity]
    url = f'{PREFIX[entity]}/{record.id}/edit?q={FILTER}'
    payload = form_payload(signed_in.get(url).get_data(as_text=True))
    response = signed_in.post(url, data=payload)
    assert response.status_code == 302, (entity, response.status_code,
                                         response.get_data(as_text=True)[:400])
    location = response.headers['Location']
    assert location.split('?')[0].rstrip('/').endswith(f'/{record.id}'), location
    assert carries_filter(location), location


@pytest.mark.parametrize('entity', ENTITIES)
def test_cancel_keeps_the_filters(signed_in, records, entity):
    record = records[entity]
    body = signed_in.get(f'{PREFIX[entity]}/{record.id}/edit?q={FILTER}').get_data(as_text=True)
    cancel = re.search(r'<a href="([^"]+)"\s+class="btn btn-[a-z]+">Cancel</a>', body)
    assert cancel, f'no Cancel on the {entity} form'
    assert carries_filter(cancel.group(1).replace('&amp;', '&')), cancel.group(1)


# ── the whole round trip ───────────────────────────────────────────────────

@pytest.mark.parametrize('entity', ENTITIES)
def test_list_record_edit_save_back_lands_on_the_filtered_list(signed_in, records, entity):
    """The reported bug, end to end, following the links the pages render."""
    record = records[entity]
    start = f'{PREFIX[entity]}/?q={FILTER}'

    detail = next(h for h in hrefs(signed_in.get(start).get_data(as_text=True))
                  if h.split('?')[0].rstrip('/').endswith(f'/{record.id}'))
    detail = detail.replace('&amp;', '&')
    edit = next(h for h in hrefs(signed_in.get(detail).get_data(as_text=True))
                if h.split('?')[0].endswith(f'/{record.id}/edit')).replace('&amp;', '&')

    payload = form_payload(signed_in.get(edit).get_data(as_text=True))
    saved = signed_in.post(edit, data=payload).headers['Location']

    page = signed_in.get(saved).get_data(as_text=True)
    back = re.search(r'<a href="([^"]+)"\s+class="btn btn-secondary page-back"', page)
    assert back, 'no Back on the record after saving'
    back_url = back.group(1).replace('&amp;', '&')
    assert back_url.split('?')[0].rstrip('/') == PREFIX[entity], back_url
    assert carries_filter(back_url), f'Back lost the filter: {back_url}'


def test_the_page_number_is_still_never_carried(signed_in, records):
    """carried_list_args drops `page` for every route now, where the detail
    routes used to pass the query string through untouched."""
    wo = records['work_orders']
    body = signed_in.get(f'/work-orders/{wo.id}?q={FILTER}&page=4').get_data(as_text=True)
    edit = next(h for h in hrefs(body) if h.split('?')[0].endswith(f'/{wo.id}/edit'))
    assert 'page=' not in edit, edit
    assert carries_filter(edit.replace('&amp;', '&'))


# ── creating ───────────────────────────────────────────────────────────────
#
# The create form posts to its own URL like the edit form does, so the filters
# that came in on "+ New" arrive with the save.

NAME_FIELD = {'work_orders': 'title', 'pms': 'name', 'job_plans': 'name',
              'assets': 'name', 'locations': 'name'}


def filled_create_form(client, entity, url):
    payload = form_payload(client.get(url).get_data(as_text=True))
    payload[NAME_FIELD[entity]] = f'{FILTER} new {entity}'
    if entity == 'pms':
        payload['interval_days'] = '30'
        payload['next_due_date'] = date.today().isoformat()
    return payload


@pytest.mark.parametrize('entity', ENTITIES)
def test_the_new_button_carries_the_filters(signed_in, records, entity):
    body = signed_in.get(f'{PREFIX[entity]}/?q={FILTER}').get_data(as_text=True)
    new = [h for h in hrefs(body) if h.split('?')[0].rstrip('/').endswith('/new')
           and h.startswith(PREFIX[entity])]
    assert new, f'no + New on the {entity} list'
    assert all(carries_filter(h.replace('&amp;', '&')) for h in new), new


@pytest.mark.parametrize('entity', ENTITIES)
def test_creating_lands_on_the_new_record_with_the_filters(signed_in, records, entity):
    url = f'{PREFIX[entity]}/new?q={FILTER}'
    response = signed_in.post(url, data=filled_create_form(signed_in, entity, url))
    assert response.status_code == 302, (entity, response.get_data(as_text=True)[:400])
    location = response.headers['Location']
    assert re.search(r'/\d+$', location.split('?')[0].rstrip('/')), location
    assert carries_filter(location), location


@pytest.mark.parametrize('entity', ENTITIES)
def test_cancelling_a_new_record_keeps_the_filters(signed_in, records, entity):
    body = signed_in.get(f'{PREFIX[entity]}/new?q={FILTER}').get_data(as_text=True)
    cancel = re.search(r'<a href="([^"]+)"\s+class="btn btn-[a-z]+">Cancel</a>', body)
    assert cancel and carries_filter(cancel.group(1).replace('&amp;', '&')), cancel


@pytest.mark.parametrize('entity', ENTITIES)
def test_new_save_back_lands_on_the_filtered_list(signed_in, records, entity):
    """The round trip for creating, following the rendered links."""
    start = f'{PREFIX[entity]}/?q={FILTER}'
    new = next(h for h in hrefs(signed_in.get(start).get_data(as_text=True))
               if h.split('?')[0].rstrip('/').endswith('/new')
               and h.startswith(PREFIX[entity])).replace('&amp;', '&')
    saved = signed_in.post(new, data=filled_create_form(signed_in, entity, new)).headers['Location']
    page = signed_in.get(saved).get_data(as_text=True)
    back = re.search(r'<a href="([^"]+)"\s+class="btn btn-secondary page-back"', page).group(1)
    back = back.replace('&amp;', '&')
    assert back.split('?')[0].rstrip('/') == PREFIX[entity] and carries_filter(back), back


# ── deleting ───────────────────────────────────────────────────────────────
#
# The delete form posts to an explicit action, unlike the edit and create
# forms, so it only has the filters if that action's URL carries them.

@pytest.mark.parametrize('entity', ENTITIES)
def test_the_delete_form_posts_with_the_filters(signed_in, records, entity):
    record = deletable(records, entity)
    body = signed_in.get(f'{PREFIX[entity]}/{record.id}?q={FILTER}').get_data(as_text=True)
    action = re.search(rf'action="([^"]*/{record.id}/delete[^"]*)"', body)
    assert action, f'no delete form on the {entity} record'
    assert carries_filter(action.group(1).replace('&amp;', '&')), action.group(1)


@pytest.mark.parametrize('entity', ENTITIES)
def test_deleting_returns_to_the_filtered_list(signed_in, records, entity):
    record = deletable(records, entity)
    response = signed_in.post(f'{PREFIX[entity]}/{record.id}/delete?q={FILTER}',
                              data={'csrf_token': CSRF})
    location = response.headers['Location']
    assert location.split('?')[0].rstrip('/') == PREFIX[entity], location
    assert carries_filter(location), location


def test_a_refused_delete_returns_to_the_record_with_the_filters(signed_in, records):
    """The location holds an asset, so it cannot be deleted — that path sends
    you back to the record, and it has to keep the filters too."""
    loc = records['locations']
    response = signed_in.post(f'/locations/{loc.id}/delete?q={FILTER}',
                              data={'csrf_token': CSRF})
    location = response.headers['Location']
    assert location.split('?')[0].rstrip('/').endswith(f'/locations/{loc.id}'), location
    assert carries_filter(location), location


# ── what must not carry them ───────────────────────────────────────────────

def test_a_link_to_a_different_kind_of_record_does_not_carry_them(signed_in, records):
    """Filters belong to the list they were set on. "+ New WO" on an asset page
    carrying the asset list's `q` would arrive as a search on the work order
    list — a filter nobody set there. Pinned so nobody "completes the pattern"."""
    asset = records['assets']
    body = signed_in.get(f'/assets/{asset.id}?q={FILTER}&show=all').get_data(as_text=True)
    new_wo = [h for h in hrefs(body) if h.split('?')[0].rstrip('/') == '/work-orders/new']
    assert new_wo, 'no + New WO on the asset page'
    assert not any('q=' in h or 'show=' in h for h in new_wo), new_wo


def test_the_picker_plus_buttons_do_not_carry_them(signed_in, records):
    """They create a *related* record in the modal; the work order list's
    filters mean nothing on the asset create page."""
    body = signed_in.get(f'/work-orders/new?q={FILTER}').get_data(as_text=True)
    plus = re.findall(r'<a class="picker-add" href="([^"]+)"', body)
    assert plus, 'no picker + buttons'
    assert not any('q=' in h for h in plus), plus


def test_clear_them_really_clears(signed_in, records):
    """The empty state's one link that must drop the filters."""
    body = signed_in.get('/work-orders/?q=nothingmatchesthis').get_data(as_text=True)
    clear = re.search(r'<a href="([^"]+)">Clear them</a>', body).group(1)
    assert '?' not in clear, clear


def test_create_one_carries_them(signed_in, db):
    """On a genuinely empty table, Create one is a link to the same kind of
    record, so it carries the filters like + New does."""
    body = signed_in.get(f'/job-plans/?q={FILTER}').get_data(as_text=True)
    create = re.search(r'<a href="([^"]+)">Create one\.</a>', body).group(1)
    assert carries_filter(create.replace('&amp;', '&')), create
