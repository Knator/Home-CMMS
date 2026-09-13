"""The "Support This Project" link and its dialog.

The trigger is an ordinary link to the real destination and the dialog is a
progressive enhancement, so the tests pin both halves: that the link works on
its own, and that the dialog is not nested somewhere the mobile drawer would
drag it off-screen.
"""
import pathlib

import pytest

from tests.conftest import make_user

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def admin_client(client, db, login):
    make_user('admin', role='admin', password='Password123!')
    login('admin', 'Password123!')
    return client


def test_trigger_and_dialog_render(admin_client):
    html = admin_client.get('/').get_data(as_text=True)
    assert 'Support This Project' in html
    assert "Click here if you'd like to support this work" in html
    assert 'OR go to this link in any browser' in html
    assert html.count('https://buymeacoffee.com/knator') == 3   # trigger, button, field
    assert 'Buy me a coffee' in html
    assert 'id="support-dialog"' in html
    assert 'data-copy-target' in html


def test_the_dialog_is_outside_the_sidebar(admin_client):
    """Below 860px the sidebar is transformed, which would capture a fixed-
    position child and slide the dialog off-screen with the drawer."""
    html = admin_client.get('/').get_data(as_text=True)
    sidebar = html.index('<div class="sidebar"')
    sidebar_end = html.index('<div class="main-content">')
    dialog = html.index('id="support-dialog"')
    assert not (sidebar < dialog < sidebar_end), 'dialog is nested in the sidebar'


def test_the_trigger_works_without_javascript(admin_client):
    """It is a real link to the real destination, not a bare button."""
    html = admin_client.get('/').get_data(as_text=True)
    i = html.index('data-support-open')
    tag = html[html.rindex('<a', 0, i):html.index('>', i) + 1]
    assert 'href="https://buymeacoffee.com/knator"' in tag
    assert 'rel="noopener noreferrer"' in tag
    assert 'target="_blank"' in tag


def test_the_dialog_starts_hidden(admin_client):
    html = admin_client.get('/').get_data(as_text=True)
    tag = html[html.index('<div class="support-dialog"'):]
    assert 'hidden' in tag[:tag.index('>')]


# ── the trap this feature fell into ────────────────────────────────────────

def test_anything_hidden_in_a_template_is_really_hidden():
    """`hidden` hides an element through the user agent's `[hidden] {display:
    none}`, a plain type selector that loses to any class rule setting `display`.

    Get that wrong and the element is displayed permanently while JavaScript
    that sets `hidden` appears to do nothing — which for the support dialog
    meant an unclosable overlay covering every page. Invisible from the server,
    so it is checked here: every class rendered with `hidden` must either set no
    `display` of its own, or carry a matching `[hidden]` rule.
    """
    import re

    css = (ROOT / 'app' / 'static' / 'css' / 'main.css').read_text()

    classes = set()
    for template in (ROOT / 'app' / 'templates').rglob('*.html'):
        for tag in re.findall(r'<[a-zA-Z][^>]*>', template.read_text()):
            if not re.search(r'\shidden(\s|>|/)', tag):
                continue
            found = re.search(r'class="([^"]*)"', tag)
            if found:
                classes.update(c for c in found.group(1).split() if '{' not in c)

    assert 'support-dialog' in classes, 'the dialog stopped being hidden by default'

    unguarded = []
    for cls in sorted(classes):
        rule = re.search(r'\.' + re.escape(cls) + r'\s*\{([^}]*)\}', css)
        if not rule:
            continue
        if re.search(r'(^|;)\s*display\s*:', rule.group(1)) \
                and f'.{cls}[hidden]' not in css:
            unguarded.append(cls)

    assert not unguarded, (
        'these classes set `display` and are rendered with `hidden`, so the '
        f'attribute will not hide them: {unguarded}. Add `.<class>[hidden] '
        '{ display: none; }`.'
    )
