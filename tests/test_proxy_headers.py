"""What the application believes when it is behind a reverse proxy.

`TRUST_PROXY_HEADERS` exists because without it every request appears to come
from the proxy, which makes the per-address sign-in limit useless. But trust has
to be granted per header, not wholesale: the proxy sets some of them and passes
others straight through from the client.
"""
import os
import tempfile

import pytest

from app import create_app
from app.extensions import db
from tests.conftest import CSRF, make_user, prime_csrf


@pytest.fixture
def proxied():
    """An instance configured as it is behind Cloudflare."""
    tmp = tempfile.TemporaryDirectory()
    application = create_app(config_overrides={
        'TESTING': True,
        'SECRET_KEY': 'proxy-test',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{os.path.join(tmp.name, "t.db")}',
        'UPLOAD_FOLDER': os.path.join(tmp.name, 'uploads'),
        'SCHEDULER_ENABLED': False,
        'TRUST_PROXY_HEADERS': True,
    })
    application._tmp = tmp
    with application.app_context():
        db.create_all()
        make_user('tester', role='user', password='Password123!')
    yield application


def test_a_forged_host_header_cannot_move_the_application(proxied):
    """The live bug. Nothing on the way in overwrites `X-Forwarded-Host`, so a
    client's own value arrived intact and became the host every `_external` URL
    was built from — including the server advertised by the public API
    specification."""
    client = proxied.test_client()
    spec = client.get('/api/v1/openapi.json',
                      headers={'X-Forwarded-Host': 'evil.example'}).get_json()
    servers = [s.get('url', '') for s in spec.get('servers', [])]
    assert not any('evil.example' in url for url in servers), (
        f'a forged X-Forwarded-Host rewrote the advertised server: {servers}'
    )


def test_a_forged_prefix_cannot_move_the_application(proxied):
    """`X-Forwarded-Prefix` is passed through the same way and rewrites the
    path every generated URL is rooted at."""
    client = proxied.test_client()
    response = client.get('/admin/users',
                          headers={'X-Forwarded-Prefix': '/evil'})
    assert '/evil' not in response.headers.get('Location', '')


def test_the_real_client_address_is_still_recorded(proxied):
    """The reason TRUST_PROXY_HEADERS exists at all — narrowing it must not
    quietly switch this back off, or every visitor shares one address again and
    a single failing user locks out everybody."""
    from app.models.auth_attempt import AuthAttempt

    client = proxied.test_client()
    prime_csrf(client)
    client.post('/auth/login',
                data={'username': 'nobody', 'password': 'wrong',
                      'csrf_token': CSRF},
                headers={'X-Forwarded-For': '198.51.100.7'})

    with proxied.app_context():
        attempt = AuthAttempt.query.order_by(AuthAttempt.id.desc()).first()
        assert attempt is not None, 'the failed sign-in was not recorded'
        assert attempt.ip_address == '198.51.100.7', attempt.ip_address


def test_only_the_two_safe_headers_are_trusted(proxied):
    """Stated on the middleware itself, so a future widening is deliberate."""
    fix = proxied.wsgi_app
    assert (fix.x_for, fix.x_proto) == (1, 1)
    assert (fix.x_host, fix.x_prefix) == (0, 0)


def test_nothing_is_trusted_when_the_setting_is_off(proxied):
    """The default: without a proxy in front, believing these headers would let
    any client claim any address and dodge the sign-in limit."""
    tmp = tempfile.TemporaryDirectory()
    plain = create_app(config_overrides={
        'TESTING': True,
        'SECRET_KEY': 'no-proxy',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{os.path.join(tmp.name, "t.db")}',
        'UPLOAD_FOLDER': os.path.join(tmp.name, 'uploads'),
        'SCHEDULER_ENABLED': False,
    })
    assert not hasattr(plain.wsgi_app, 'x_for'), 'ProxyFix applied without the setting'
