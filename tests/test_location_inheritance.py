"""Deriving a location from another record, on the forms that do it.

initAssetLocationLink() is driven entirely by attributes the templates set, so
a rename on either side disables the feature silently rather than failing — the
form simply stops filling anything in. These pin the contract from both ends.
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN_JS = (ROOT / 'app' / 'static' / 'js' / 'main.js').read_text()
TEMPLATES = ROOT / 'app' / 'templates'


def read_template(name):
    return (TEMPLATES / name).read_text()


def test_asset_form_inherits_the_parents_location():
    """Picking a parent asset fills a blank Location, as work orders do."""
    html = read_template('assets/form.html')
    assert 'id="parent_id"' in html
    assert 'data-summary-url' in html
    # Without this, choosing a parent would overwrite a location the user set
    # by hand — a child asset may legitimately sit somewhere else.
    assert 'data-only-when-empty' in html
    # The JS finds the destination by id; the field previously had only a name.
    assert 'id="location_id"' in html
    assert 'id="location-hint"' in html


@pytest.mark.parametrize('template', ['work_orders/form.html', 'pms/form.html'])
def test_asset_driven_forms_re_derive_on_every_change(template):
    """These derive location *from* the asset, so they must not opt in to
    only-when-empty: that would stop the location following the asset."""
    html = read_template(template)
    assert 'data-summary-url' in html
    assert 'id="location_id"' in html
    assert 'data-only-when-empty' not in html


def test_the_js_reads_exactly_the_attributes_the_templates_set():
    assert "querySelectorAll('select[data-summary-url]')" in MAIN_JS
    assert 'dataset.onlyWhenEmpty' in MAIN_JS
    assert "getElementById('location_id')" in MAIN_JS
    assert "getElementById('location-hint')" in MAIN_JS


def test_the_summary_endpoint_the_forms_point_at_still_exists(app):
    """data-summary-url is built with url_for('assets.summary'), so the
    endpoint name is part of the contract too."""
    with app.test_request_context():
        from flask import url_for
        assert url_for('assets.summary', id=0).endswith('/0/summary')
