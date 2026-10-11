"""Outgoing email: the SMTP settings on the Settings page, and the test button.

Sending is exercised against a real SMTP conversation (tests/smtp_server.py)
in each of the three security modes, because the ways it goes wrong — a
STARTTLS port given SSL, a self-signed certificate, a login the server will not
offer — happen in the handshake, which a mock would only agree with.
"""
import logging
import socket
import ssl
import subprocess

import pytest
from werkzeug.datastructures import MultiDict

from app import mail
from app import settings as app_settings
from app.extensions import db as _db
from app.mail import MailConfig, MailError, send_mail
from tests.conftest import CSRF, make_user, prime_csrf
from tests.smtp_server import SMTPTestServer

USER, PASSWORD = 'mailer', 'app-password-123'


@pytest.fixture(scope='session')
def certificate(tmp_path_factory):
    """A self-signed certificate for localhost and 127.0.0.1."""
    folder = tmp_path_factory.mktemp('tls')
    cert, key = folder / 'cert.pem', folder / 'key.pem'
    try:
        subprocess.run(
            ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
             '-keyout', str(key), '-out', str(cert), '-subj', '/CN=localhost',
             '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
            check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip('openssl is needed to make a test certificate')
    return str(cert), str(key)


def server(certificate, mode, **kwargs):
    cert, key = certificate
    kwargs.setdefault('username', USER)
    kwargs.setdefault('password', PASSWORD)
    return SMTPTestServer(mode=mode, certfile=cert, keyfile=key, **kwargs)


def config(port, security, *, verify=False, username=USER, password=PASSWORD,
           host='localhost', from_name='Home CMMS'):
    return MailConfig(host, port, security, username, password,
                      'cmms@example.com', from_name, verify)


# ── sending, in each mode ──────────────────────────────────────────────────

@pytest.mark.parametrize('mode, security, encrypted', [
    ('starttls', 'starttls', True),
    ('ssl', 'ssl', True),
    ('plain', 'none', False),
])
def test_each_security_mode_delivers(certificate, mode, security, encrypted):
    with server(certificate, mode) as smtp:
        send_mail('kevin@example.com', 'Filter due', 'Change it.',
                  config=config(smtp.port, security))
    [sent] = smtp.messages
    assert sent['secure'] is encrypted
    assert sent['from'] == 'cmms@example.com'
    assert sent['to'] == ['kevin@example.com']
    assert smtp.logins == [(USER, PASSWORD)]
    message = sent['message']
    assert message['From'] == 'Home CMMS <cmms@example.com>'
    assert message['To'] == 'kevin@example.com'
    assert message['Subject'] == 'Filter due'
    assert message['Message-ID'].endswith('@example.com>')
    assert message.get_content().strip() == 'Change it.'


def test_no_username_means_no_login(certificate):
    """A relay that trusts its own network needs none, and many refuse one."""
    with server(certificate, 'starttls', username=None, password=None) as smtp:
        send_mail('kevin@example.com', 'x', 'y', config=config(smtp.port, 'starttls',
                                                                username='', password=''))
    assert smtp.logins == []
    assert len(smtp.messages) == 1


def test_a_from_name_with_specials_is_quoted(certificate):
    with server(certificate, 'starttls') as smtp:
        send_mail('kevin@example.com', 'x', 'y',
                  config=config(smtp.port, 'starttls', from_name='Coleman, House'))
    assert smtp.messages[0]['message']['From'] == '"Coleman, House" <cmms@example.com>'


def test_a_verified_certificate_is_accepted(certificate, monkeypatch):
    """Verification on, against a CA the client trusts: the hostname check and
    the chain both pass. Proves verification is on the path, not merely that
    turning it off works."""
    cert, _key = certificate
    real = ssl.create_default_context
    monkeypatch.setattr(mail.ssl, 'create_default_context', lambda: real(cafile=cert))
    with server(certificate, 'starttls') as smtp:
        send_mail('kevin@example.com', 'x', 'y',
                  config=config(smtp.port, 'starttls', verify=True))
    assert len(smtp.messages) == 1


# ── failures, as sentences ─────────────────────────────────────────────────

def failure(cfg):
    with pytest.raises(MailError) as caught:
        send_mail('kevin@example.com', 'x', 'y', config=cfg)
    return str(caught.value)


def test_a_wrong_password(certificate):
    with server(certificate, 'starttls') as smtp:
        message = failure(config(smtp.port, 'starttls', password='wrong'))
    assert 'rejected the username or password' in message
    assert 'app password' in message
    assert smtp.messages == []


def test_an_untrusted_certificate(certificate):
    with server(certificate, 'starttls') as smtp:
        message = failure(config(smtp.port, 'starttls', verify=True))
    assert 'could not be verified' in message
    assert 'self-signed' in message


def test_ssl_against_a_starttls_port(certificate):
    with server(certificate, 'starttls') as smtp:
        message = failure(config(smtp.port, 'ssl'))
    assert 'secure connection' in message and 'Security setting' in message


def test_starttls_where_none_is_offered(certificate):
    with server(certificate, 'starttls', offer_starttls=False) as smtp:
        message = failure(config(smtp.port, 'starttls'))
    assert 'does not offer STARTTLS' in message


def test_a_login_the_server_will_not_offer(certificate):
    """Unencrypted, with a username: this server only offers AUTH after TLS."""
    with server(certificate, 'starttls') as smtp:
        message = failure(config(smtp.port, 'none'))
    assert 'does not offer a login' in message


def test_a_refused_sender(certificate):
    with server(certificate, 'starttls', refuse_sender='cmms@example.com') as smtp:
        message = failure(config(smtp.port, 'starttls'))
    assert 'refused to send from cmms@example.com' in message


def test_an_unknown_host():
    message = failure(config(587, 'starttls', host='no-such-host.invalid'))
    assert 'could not be found' in message


def test_a_closed_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]       # free, and nothing listening
    message = failure(config(port, 'starttls', host='127.0.0.1'))
    assert 'refused the connection' in message


def test_a_server_that_never_answers_gives_up(monkeypatch):
    """The single worker must not be held: the wait is bounded."""
    monkeypatch.setattr(mail, 'SEND_TIMEOUT', 1)
    with socket.socket() as silent:
        silent.bind(('127.0.0.1', 0))
        silent.listen()                      # accepts, never says a word
        message = failure(config(silent.getsockname()[1], 'starttls', host='127.0.0.1'))
    assert 'did not answer within 1 seconds' in message


@pytest.mark.parametrize('port, security, hint', [
    (587, 'ssl', 'Port 587 usually means STARTTLS'),
    (465, 'starttls', 'Port 465 usually needs SSL/TLS'),
    (2525, 'ssl', None),
])
def test_a_mismatched_port_is_named(port, security, hint):
    explained = mail.explain(ssl.SSLError('wrong version number'), config(port, security))
    if hint:
        assert hint in explained
    else:
        assert 'usually' not in explained


def test_nothing_is_sent_unconfigured():
    with pytest.raises(MailError, match='No SMTP server'):
        send_mail('k@example.com', 'x', 'y', config=config(25, 'none', host=''))


def test_a_header_cannot_be_injected(certificate):
    with server(certificate, 'starttls') as smtp:
        with pytest.raises(MailError):
            send_mail('k@example.com', 'Hi\r\nBcc: everyone@example.com', 'y',
                      config=config(smtp.port, 'starttls'))
    assert smtp.messages == []


def test_the_password_stays_out_of_logs_and_reprs(certificate, caplog):
    cfg = config(1, 'starttls', host='127.0.0.1', password='s3cret-pass')
    assert 's3cret-pass' not in repr(cfg)
    with caplog.at_level(logging.WARNING, logger='app.mail'):
        failure(cfg)
    assert caplog.records, 'a failure should be logged'
    assert 's3cret-pass' not in caplog.text


# ── the Settings page ─────────────────────────────────────────────────────

@pytest.fixture
def owner(client, db, login):
    make_user('owner', role='super_admin', password='Password123!')
    login('owner', 'Password123!')
    prime_csrf(client)
    return client


def save(client, **fields):
    data = {'csrf_token': CSRF, 'upload_limit_enabled': '1', 'max_upload_mb': '100',
            'update_check_enabled': '1', 'smtp_host': 'smtp.example.com',
            'smtp_port': '587', 'smtp_security': 'starttls', 'smtp_username': USER,
            'smtp_password': '', 'smtp_from_address': 'cmms@example.com',
            'smtp_from_name': 'Home CMMS', 'smtp_verify_tls': '1'}
    data.update(fields)
    return client.post('/admin/settings', data=MultiDict({k: v for k, v in data.items()
                                                          if v is not None}))


def stored(key):
    _db.session.expire_all()
    app_settings.begin_request()
    return app_settings.get(key)


def flashes(client):
    with client.session_transaction() as sess:
        return [message for _category, message in sess.get('_flashes', [])]


def test_the_card_is_on_settings(owner):
    body = owner.get('/admin/settings').get_data(as_text=True)
    assert 'Email (SMTP)' in body
    assert 'name="smtp_host"' in body and 'name="send_test"' in body
    assert 'STARTTLS — usually port 587' in body


def test_admins_cannot_reach_it(client, db, login):
    make_user('helper', role='admin', password='Password123!')
    login('helper', 'Password123!')
    prime_csrf(client)
    save(client, smtp_password='nope')
    assert stored('smtp_host') == ''
    assert stored('smtp_password') == ''


def test_saving_stores_the_server(owner):
    save(owner, smtp_password=PASSWORD, smtp_security='ssl', smtp_port='465')
    assert (stored('smtp_host'), stored('smtp_port'), stored('smtp_security')) \
        == ('smtp.example.com', 465, 'ssl')
    assert stored('smtp_username') == USER
    assert stored('smtp_password') == PASSWORD
    assert stored('smtp_from_address') == 'cmms@example.com'
    assert stored('smtp_verify_tls') is True


def test_the_password_is_never_sent_back_to_the_page(owner):
    save(owner, smtp_password=PASSWORD)
    body = owner.get('/admin/settings').get_data(as_text=True)
    assert PASSWORD not in body
    assert 'Saved — leave blank to keep' in body


def test_a_blank_password_keeps_the_saved_one(owner):
    save(owner, smtp_password=PASSWORD)
    save(owner, smtp_password='')
    assert stored('smtp_password') == PASSWORD
    save(owner, smtp_password='a-new-one')
    assert stored('smtp_password') == 'a-new-one'


def test_no_username_removes_the_password(owner):
    save(owner, smtp_password=PASSWORD)
    save(owner, smtp_username='', smtp_password='')
    assert stored('smtp_password') == ''


def test_unticked_certificate_check_is_saved_as_off(owner):
    save(owner, smtp_verify_tls=None)
    assert stored('smtp_verify_tls') is False


def test_a_blank_server_switches_email_off_without_the_rest(owner):
    response = save(owner, smtp_host='', smtp_from_address='', smtp_port='')
    assert response.status_code == 302
    assert stored('smtp_host') == ''


@pytest.mark.parametrize('fields, complaint', [
    ({'smtp_host': 'smtp example.com'}, 'no spaces'),
    ({'smtp_port': '70000'}, 'between 1 and 65535'),
    ({'smtp_port': ''}, 'between 1 and 65535'),
    ({'smtp_from_address': ''}, 'address email should come from'),
    ({'smtp_from_address': 'not-an-address'}, 'address email should come from'),
    ({'smtp_security': 'telnet'}, 'how the SMTP connection is secured'),
    ({'smtp_from_name': 'Home\r\nBcc: x@example.com'}, 'single line'),
])
def test_bad_input_is_refused_and_nothing_saved(owner, fields, complaint):
    response = save(owner, smtp_password=PASSWORD, **fields)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert complaint in body
    assert stored('smtp_host') == ''              # nothing written
    assert stored('smtp_password') == ''
    # What was typed survives the round trip — except the password.
    assert f'value="{fields.get("smtp_host", "smtp.example.com")}"' in body
    assert f'value="{USER}"' in body
    assert PASSWORD not in body


def test_settings_does_not_touch_preferences(owner):
    save(owner)
    assert stored('allow_archived_deletion') is True
    assert stored('pm_stall_on_open') is True


def test_preferences_does_not_touch_email(owner):
    save(owner, smtp_password=PASSWORD)
    owner.post('/admin/preferences', data={'csrf_token': CSRF, 'default_grace_days': '3'})
    assert stored('smtp_host') == 'smtp.example.com'
    assert stored('smtp_password') == PASSWORD


# ── the test button ───────────────────────────────────────────────────────

def test_the_button_saves_then_sends(owner, certificate):
    with server(certificate, 'starttls') as smtp:
        save(owner, smtp_host='127.0.0.1', smtp_port=str(smtp.port),
             smtp_password=PASSWORD, smtp_verify_tls=None,
             send_test='1', test_to='kevin@example.com')
    assert stored('smtp_host') == '127.0.0.1'           # saved first
    [sent] = smtp.messages
    assert sent['to'] == ['kevin@example.com']
    assert sent['message']['Subject'] == 'Home CMMS test email'
    assert 'sent by owner' in sent['message'].get_content()
    assert any('Test email sent to kevin@example.com' in m for m in flashes(owner))


def test_a_failed_test_says_why_and_keeps_the_settings(owner, certificate):
    with server(certificate, 'starttls') as smtp:
        save(owner, smtp_host='127.0.0.1', smtp_port=str(smtp.port),
             smtp_password='wrong', smtp_verify_tls=None,
             send_test='1', test_to='kevin@example.com')
    assert smtp.messages == []
    messages = flashes(owner)
    assert 'Settings saved.' in messages
    assert any(m.startswith('The test email was not sent.')
               and 'rejected the username or password' in m for m in messages)
    assert stored('smtp_host') == '127.0.0.1'


def test_the_test_needs_somewhere_to_go(owner):
    response = save(owner, smtp_password=PASSWORD, send_test='1', test_to='')
    assert 'Enter the address to send the test email to.' in response.get_data(as_text=True)
    assert stored('smtp_host') == ''


def test_a_test_with_no_server_says_so(owner):
    save(owner, smtp_host='', smtp_from_address='', send_test='1',
         test_to='kevin@example.com')
    assert any('No SMTP server is set' in m for m in flashes(owner))


def test_the_test_address_defaults_to_yours(owner):
    body = owner.get('/admin/settings').get_data(as_text=True)
    assert 'name="test_to" maxlength="255"\n                 value="owner@example.com"' in body
