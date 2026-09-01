"""Markup the responsive layout depends on.

Rendering can only be judged in a browser, but the hooks the CSS and JS need can
be checked — and a template edit that removes one would otherwise break the
mobile layout silently.
"""
import pathlib

import pytest

from app.models.location import Location
from app.services import create_location, create_asset, create_work_order

CSS = (pathlib.Path(__file__).resolve().parent.parent
       / 'app' / 'static' / 'css' / 'main.css').read_text()


@pytest.fixture
def seeded(db, user):
    """Every list needs a row, or it renders its empty state and the table
    markup under test never appears."""
    from datetime import date
    from app.models.job_plan import JobPlan
    from app.models.pm import PM

    loc = create_location(name='Garage')
    plan = JobPlan(name='Checklist')
    db.session.add_all([loc, plan])
    db.session.flush()
    asset = create_asset(name='Furnace', location_id=loc.id)
    db.session.add(PM(name='Annual', asset_id=asset.id, interval_days=365,
                      next_due_date=date.today()))
    db.session.commit()
    create_work_order(title='Fix it', asset_id=asset.id, location_id=loc.id)
    return {'location': loc.id, 'asset': asset.id}


# ── the drawer ─────────────────────────────────────────────────────────────

def test_every_page_has_the_nav_toggle(client, seeded, login):
    login()
    for path in ('/', '/assets/', '/locations/', '/work-orders/', '/pms/', '/job-plans/'):
        body = client.get(path).get_data(as_text=True)
        assert 'id="nav-toggle"' in body, path
        assert 'id="sidebar-backdrop"' in body, path
        assert 'id="sidebar"' in body, path


def test_nav_toggle_is_labelled_for_screen_readers(client, seeded, login):
    login()
    body = client.get('/').get_data(as_text=True)
    assert 'aria-controls="sidebar"' in body
    assert 'aria-expanded="false"' in body
    assert 'aria-label="Open menu"' in body


def test_every_page_declares_a_viewport(client, seeded, login):
    login()
    for path in ('/', '/assets/', '/work-orders/new'):
        assert 'name="viewport"' in client.get(path).get_data(as_text=True), path


def test_login_page_is_responsive_too(client):
    """It renders outside base.html, so it needs its own viewport tag."""
    body = client.get('/auth/login').get_data(as_text=True)
    assert 'name="viewport"' in body


# ── layout hooks ───────────────────────────────────────────────────────────

def test_dashboard_columns_are_a_class_not_inline(client, seeded, login):
    """An inline two-column grid could not collapse on a phone."""
    login()
    body = client.get('/').get_data(as_text=True)
    assert 'split-grid' in body
    assert 'grid-template-columns:1fr 1fr' not in body


def test_form_shells_use_a_class(client, seeded, login):
    login()
    for path in ('/assets/new', '/work-orders/new', '/pms/new', '/job-plans/new'):
        assert 'form-shell' in client.get(path).get_data(as_text=True), path


def test_wide_lists_mark_their_secondary_columns(client, seeded, login):
    login()
    for path in ('/work-orders/', '/assets/', '/pms/', '/job-plans/', '/locations/'):
        assert 'col-hide-sm' in client.get(path).get_data(as_text=True), path


def test_primary_columns_are_never_hidden(client, seeded, login):
    """Whatever drops out, the row still has to be identifiable and clickable."""
    login()
    body = client.get('/work-orders/').get_data(as_text=True)
    for keep in ('<th>WO #</th>', '<th>Title</th>', '<th>Status</th>', '<th>Due</th>'):
        assert keep in body, keep


def test_tables_stay_inside_a_scroll_container(client, seeded, login):
    login()
    for path in ('/work-orders/', '/assets/', '/locations/'):
        assert 'table-wrap' in client.get(path).get_data(as_text=True), path


# ── the stylesheet itself ──────────────────────────────────────────────────

def test_stylesheet_has_the_layout_breakpoints():
    assert '@media (max-width: 860px)' in CSS
    assert '@media (max-width: 700px)' in CSS
    assert '@media (max-width: 480px)' in CSS


def test_sidebar_becomes_a_drawer():
    section = CSS[CSS.index('@media (max-width: 860px)'):]
    assert 'transform: translateX(-100%)' in section
    assert 'body.nav-open .sidebar' in section


def test_inputs_avoid_the_ios_zoom():
    """Focusing a field under 16px makes iOS zoom the whole page."""
    section = CSS[CSS.index('@media (max-width: 860px)'):]
    assert 'font-size: 16px' in section


def test_viewport_height_uses_dvh():
    """100vh includes mobile browser chrome, cutting off the bottom of the page."""
    assert '100dvh' in CSS
