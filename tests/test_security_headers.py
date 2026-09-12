"""Security response headers.

Set by the application rather than at a proxy, because this is self-hosted:
most instances have no proxy, and one that does may still be reached directly
on the LAN — which bypasses it entirely. Headers that travel with the app apply
on every path.
"""
import io

import pytest

from app.models.attachment import Attachment
from app.services import create_work_order
from tests.conftest import CSRF, prime_csrf

EXPECTED = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'SAMEORIGIN',
    'Referrer-Policy': 'same-origin',
}


@pytest.fixture
def signed_in(client, db, user, login):
    login()
    prime_csrf(client)
    return client


def csp(response):
    return response.headers.get('Content-Security-Policy', '')


# ── present everywhere ─────────────────────────────────────────────────────

@pytest.mark.parametrize('path', ['/auth/login', '/api/v1/docs'])
def test_headers_are_set_even_without_a_session(client, db, path):
    """An unauthenticated page is the one most likely to be framed or probed."""
    response = client.get(path)
    for header, value in EXPECTED.items():
        assert response.headers.get(header) == value, header
    assert csp(response)


@pytest.mark.parametrize('path', ['/', '/work-orders/', '/assets/', '/admin/settings'])
def test_headers_are_set_on_application_pages(signed_in, path):
    response = signed_in.get(path, follow_redirects=True)
    for header, value in EXPECTED.items():
        assert response.headers.get(header) == value, f'{path}: {header}'


def test_headers_are_set_on_error_responses(client, db):
    """A 404 is still a page an attacker can get rendered."""
    response = client.get('/no-such-page')
    assert response.status_code == 404
    assert response.headers.get('X-Frame-Options') == 'SAMEORIGIN'


# ── the directives that do the work ────────────────────────────────────────

def test_clickjacking_is_blocked_but_the_modal_still_works(client, db):
    """frame-ancestors must be 'self', never 'none': the create-record modal
    frames the app in itself. Destructive admin actions are one-click POSTs,
    and a confirm() dialog is no defence against an invisible framed page."""
    policy = csp(client.get('/auth/login'))
    assert "frame-ancestors 'self'" in policy
    assert "frame-ancestors 'none'" not in policy
    assert "frame-src 'self'" in policy


@pytest.mark.parametrize('directive', [
    "form-action 'self'",   # an injected form cannot post CSRF'd data offsite
    "base-uri 'self'",      # <base> cannot silently repoint relative URLs
    "object-src 'none'",    # no plugin/embed vectors
    "default-src 'self'",
])
def test_the_directives_that_hold_without_removing_inline_script(client, db, directive):
    """These are independent of 'unsafe-inline', so they protect today rather
    than after a refactor."""
    assert directive in csp(client.get('/auth/login'))


def test_inline_script_is_still_permitted_and_that_is_deliberate(client, db):
    """~35 inline handlers remain in the templates. Documented as a known
    compromise so this reads as a decision, not an oversight — tighten it by
    removing them, not by breaking every page."""
    assert "script-src 'self' 'unsafe-inline'" in csp(client.get('/auth/login'))


def test_nothing_external_is_allowed(client, db):
    """The app serves every asset itself, so no host needs allowing."""
    policy = csp(client.get('/auth/login'))
    assert 'http://' not in policy and 'https://' not in policy
    assert '*' not in policy


# ── HSTS belongs to whatever terminates TLS ────────────────────────────────

def test_hsts_is_not_sent_by_the_app(client, db):
    """It asserts something about transport the app cannot know, and would go
    out over plain http on a LAN address. Set it at the proxy or CDN."""
    assert 'Strict-Transport-Security' not in client.get('/auth/login').headers


# ── routes that made their own choices keep them ───────────────────────────

def test_attachment_routes_keep_their_own_caching(signed_in, db):
    wo = create_work_order(title='x', wo_type='unplanned')
    signed_in.post(f'/work-orders/{wo.id}/attachments', data={
        'csrf_token': CSRF, 'file': (io.BytesIO(b'x'), 'a.png'),
    }, content_type='multipart/form-data', follow_redirects=True)
    att = Attachment.query.one()

    thumb = signed_in.get(f'/attachments/{att.id}/thumbnail')
    # The route sets a long-lived cache deliberately; setdefault must not
    # trample it.
    assert 'max-age=604800' in thumb.headers.get('Cache-Control', '')
    assert thumb.headers['X-Content-Type-Options'] == 'nosniff'
