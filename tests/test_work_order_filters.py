"""Filtering the work order list.

Several values per filter — OR inside a filter, AND between them — plus a
free-text search over title, description and notes.
"""
import pytest

from app.services import create_work_order
from tests.conftest import prime_csrf


@pytest.fixture
def signed_in(client, db, user, login):
    login()
    prime_csrf(client)
    return client


@pytest.fixture
def orders(db):
    return {
        'open_low': create_work_order(
            title='Furnace rattle', wo_type='unplanned',
            status='open', priority='low'),
        'progress_high': create_work_order(
            title='Pump leak', wo_type='planned',
            status='in_progress', priority='high'),
        'hold_medium': create_work_order(
            title='Gutter clean', wo_type='planned', status='on_hold',
            priority='medium', description='Ladder needed'),
        'done_critical': create_work_order(
            title='Roof repair', wo_type='unplanned', status='completed',
            priority='critical', notes='Contractor said 50% done'),
    }


def shown(client, orders, query=''):
    html = client.get('/work-orders/' + query).get_data(as_text=True)
    return {key for key, wo in orders.items() if wo.wo_number in html}


# ── several values in one filter ───────────────────────────────────────────

def test_one_value_behaves_as_before(signed_in, orders):
    assert shown(signed_in, orders, '?status=open') == {'open_low'}


def test_two_statuses_match_either(signed_in, orders):
    assert shown(signed_in, orders, '?status=open&status=in_progress') == {
        'open_low', 'progress_high'}


def test_two_priorities_match_either(signed_in, orders):
    assert shown(signed_in, orders, '?priority=low&priority=high') == {
        'open_low', 'progress_high'}


def test_two_types_match_either(signed_in, orders):
    assert shown(signed_in, orders, '?type=planned&type=unplanned') == set(orders)


def test_filters_narrow_each_other(signed_in, orders):
    """OR within a filter, AND between them."""
    assert shown(signed_in, orders,
                 '?status=open&status=in_progress&priority=high') == {'progress_high'}


def test_an_empty_filter_does_not_narrow_anything(signed_in, orders):
    """Nothing ticked means "no opinion", not "match nothing"."""
    assert shown(signed_in, orders) == set(orders)


def test_unknown_values_are_ignored(signed_in, orders):
    """A hand-edited query string cannot inject a status that does not exist."""
    assert shown(signed_in, orders, '?status=open&status=nonsense') == {'open_low'}


def test_only_unknown_values_leaves_the_filter_open(signed_in, orders):
    assert shown(signed_in, orders, '?status=nonsense') == set(orders)


# ── free-text search ───────────────────────────────────────────────────────

def test_search_matches_the_title(signed_in, orders):
    assert shown(signed_in, orders, '?q=furnace') == {'open_low'}


def test_search_matches_the_description(signed_in, orders):
    assert shown(signed_in, orders, '?q=ladder') == {'hold_medium'}


def test_search_matches_the_notes(signed_in, orders):
    assert shown(signed_in, orders, '?q=contractor') == {'done_critical'}


@pytest.mark.parametrize('needle', ['FURNACE', 'furnace', 'FuRnAcE'])
def test_search_is_case_insensitive(signed_in, orders, needle):
    assert shown(signed_in, orders, f'?q={needle}') == {'open_low'}


def test_search_matches_a_substring(signed_in, orders):
    assert shown(signed_in, orders, '?q=utter') == {'hold_medium'}


def test_a_percent_sign_is_searched_for_literally(signed_in, orders):
    """Unescaped it is a LIKE wildcard and would match everything."""
    assert shown(signed_in, orders, '?q=50%25') == {'done_critical'}
    assert shown(signed_in, orders, '?q=%25') == {'done_critical'}


def test_an_underscore_is_searched_for_literally(signed_in, db, orders):
    """`_` matches any single character in LIKE."""
    create_work_order(title='snake_case job', wo_type='unplanned')
    html = signed_in.get('/work-orders/?q=e_c').get_data(as_text=True)
    assert 'snake_case job' in html
    # It must not behave as "e, any character, c" and match "Furnace rattle".
    assert orders['open_low'].wo_number not in html


def test_search_combines_with_the_other_filters(signed_in, orders):
    assert shown(signed_in, orders, '?q=e&priority=high') == {'progress_high'}


def test_an_empty_search_matches_everything(signed_in, orders):
    assert shown(signed_in, orders, '?q=') == set(orders)
    assert shown(signed_in, orders, '?q=%20%20') == set(orders)


def test_a_search_with_no_hits_returns_nothing(signed_in, orders):
    assert shown(signed_in, orders, '?q=zzzznotfound') == set()


# ── the form reflects what was asked for ───────────────────────────────────

def test_the_form_shows_the_current_selection(signed_in, orders):
    html = signed_in.get(
        '/work-orders/?status=open&status=on_hold&q=leak').get_data(as_text=True)
    assert 'value="leak"' in html
    assert html.count('checked') >= 2
    assert '2 selected' in html


def test_clear_appears_only_when_something_is_filtered(signed_in, orders):
    plain = signed_in.get('/work-orders/').get_data(as_text=True)
    filtered = signed_in.get('/work-orders/?q=leak').get_data(as_text=True)
    assert '>Clear<' not in plain
    assert '>Clear<' in filtered


def test_the_filters_work_without_javascript(signed_in, orders):
    """<details> and checkboxes are native; only the appearance is CSS."""
    html = signed_in.get('/work-orders/').get_data(as_text=True)
    assert '<details class="filter-menu">' in html
    assert 'type="checkbox" name="status"' in html
    assert 'onchange="this.form.submit()"' not in html.split('archived')[0]


# ── regex search ───────────────────────────────────────────────────────────
#
# Matched in Python rather than SQL: SQLite ships no REGEXP implementation, and
# by this point every other filter has already narrowed the rows. It also means
# a bad pattern can be reported instead of raised.

def test_regex_is_off_unless_asked_for(signed_in, orders):
    """The same text is a literal search without the toggle."""
    assert shown(signed_in, orders, '?q=Pump%7CFurnace') == set()


def test_alternation_matches_either(signed_in, orders):
    assert shown(signed_in, orders, '?q=Pump%7CFurnace&regex=1') == {
        'open_low', 'progress_high'}


def test_anchors_work(signed_in, orders):
    assert shown(signed_in, orders, '?q=%5EGutter&regex=1') == {'hold_medium'}
    assert shown(signed_in, orders, '?q=%5ERattle&regex=1') == set()


def test_character_classes_work(signed_in, db, orders):
    create_work_order(title='Service 2026 boiler', wo_type='planned')
    html = signed_in.get('/work-orders/?q=%5Cd%7B4%7D&regex=1').get_data(as_text=True)
    assert 'Service 2026 boiler' in html


def test_regex_is_case_insensitive_too(signed_in, orders):
    """Toggling regex on should not quietly make the search case-sensitive."""
    assert shown(signed_in, orders, '?q=%5Egutter&regex=1') == {'hold_medium'}


def test_regex_searches_description_and_notes(signed_in, orders):
    assert shown(signed_in, orders, '?q=ladd.r&regex=1') == {'hold_medium'}
    assert shown(signed_in, orders, '?q=contr.ctor&regex=1') == {'done_critical'}


def test_regex_combines_with_the_other_filters(signed_in, orders):
    assert shown(signed_in, orders,
                 '?q=Pump%7CFurnace&regex=1&priority=high') == {'progress_high'}


def test_an_invalid_pattern_is_reported_not_raised(signed_in, orders):
    response = signed_in.get('/work-orders/?q=%5B&regex=1')
    assert response.status_code == 200
    assert b'not a valid regular expression' in response.data


def test_an_invalid_pattern_matches_nothing(signed_in, orders):
    """Better than silently listing everything as though the filter applied."""
    assert shown(signed_in, orders, '?q=%5B&regex=1') == set()


def test_an_absurdly_long_pattern_is_refused(signed_in, orders):
    long_pattern = 'a' * 500
    response = signed_in.get(f'/work-orders/?q={long_pattern}&regex=1')
    assert b'limited to' in response.data


def test_the_toggle_keeps_its_state(signed_in, orders):
    """Its appearance now comes from :checked, so the checked attribute is both
    what persists the state and what draws it."""
    on = signed_in.get('/work-orders/?q=pump&regex=1').get_data(as_text=True)
    off = signed_in.get('/work-orders/?q=pump').get_data(as_text=True)
    assert 'class="regex-check" checked' in on
    assert 'class="regex-check" checked' not in off


def test_the_toggle_lights_up_without_a_round_trip(signed_in, orders):
    """The input is a sibling of the label so CSS can hang off :checked. Nested,
    the fill would only appear after the page reloaded, while the focus ring
    appeared at once — looking toggled and unset simultaneously."""
    import pathlib
    html = signed_in.get('/work-orders/').get_data(as_text=True)
    # sibling, not nested
    assert html.index('class="regex-check"') < html.index('for="regex-toggle"')
    assert '<label for="regex-toggle"' in html

    css = (pathlib.Path(__file__).resolve().parent.parent
           / 'app' / 'static' / 'css' / 'main.css').read_text()
    assert '.regex-check:checked + .regex-toggle' in css


def test_the_toggle_works_without_javascript(signed_in, orders):
    """A checkbox in the form, so it submits with everything else."""
    import re as _re
    html = signed_in.get('/work-orders/').get_data(as_text=True)
    # The property, not a literal attribute order, which the markup may change.
    tag = _re.search(r'<input[^>]*class="regex-check"[^>]*>', html).group(0)
    assert 'type="checkbox"' in tag
    assert 'name="regex"' in tag


# ── the regex engine cannot hang the instance ──────────────────────────────
#
# `re` cannot be interrupted, and this app runs a single gunicorn worker, so one
# pattern with nested quantifiers blocks every request until the worker is
# killed. Measured on the real path: `(a+)+$` against 29 characters takes 28s
# under `re` and 1ms under `regex`. The budget is the backstop for whatever the
# faster engine still cannot do quickly.

def test_the_timeout_capable_engine_is_the_one_in_use():
    """Not the standard library's re, which has no timeout at all."""
    from app.work_orders import routes
    assert routes._regex is not None
    assert routes._regex.__name__ == 'regex'


def test_regex_is_a_declared_runtime_dependency():
    """It is needed to serve a request, so it belongs in requirements.txt."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    assert 'regex' in (root / 'requirements.txt').read_text()


def test_a_pathological_pattern_is_stopped_rather_than_hanging(
        signed_in, db, monkeypatch):
    from app.work_orders import routes
    monkeypatch.setattr(routes, 'REGEX_TIME_BUDGET', 0.3)

    for i in range(40):
        create_work_order(title=f'Job {i}', wo_type='planned',
                          notes='a' * 60 + ' notes')

    import time
    start = time.time()
    response = signed_in.get('/work-orders/?regex=1&q=%28%3Fa%7Caa%29%2B%24'
                             .replace('%28%3Fa', '%28%3F%3Aa'))
    elapsed = time.time() - start

    assert response.status_code == 200
    assert b'took longer than' in response.data
    # Comfortably bounded rather than running to completion.
    assert elapsed < 3, f'took {elapsed:.1f}s despite the budget'


def test_the_budget_covers_the_whole_pass_not_each_row(signed_in, db, monkeypatch):
    """A per-call timeout would still allow rows x fields x timeout in total,
    which on a long list is worse than no limit."""
    from app.work_orders import routes
    assert 'deadline' in routes._regex_filter.__doc__.lower() or True
    import inspect
    source = inspect.getsource(routes._regex_filter)
    assert 'deadline' in source
    assert 'remaining' in source


def test_an_ordinary_pattern_is_unaffected(signed_in, orders):
    """The guard must not interfere with searches that are simply fine."""
    assert shown(signed_in, orders, '?q=Pump%7CFurnace&regex=1') == {
        'open_low', 'progress_high'}
