"""Outgoing email over SMTP.

Configured by a super admin on the Settings page, the way Uptime Kuma, Immich and
LubeLogger do it: server, port, how the connection is secured, an optional login
and the address mail comes from. Sent with the standard library's smtplib, so
there is nothing extra to install.

Everything that sends goes through send_mail(), so the notifications built on
this later share one connection path, one timeout and one set of error messages.

Two constraints shape it:

  * **The timeout is short.** This app runs a single gunicorn worker, so a send
    that hangs on an unreachable server blocks the whole instance for everyone
    until it gives up. SEND_TIMEOUT bounds each network wait.
  * **Failures come back as sentences.** "[SSL: WRONG_VERSION_NUMBER]" tells
    nobody that 587 wants STARTTLS rather than SSL/TLS; MailError carries what
    to do about it, and the raw detail goes to the log.
"""
import logging
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app import settings as app_settings

log = logging.getLogger(__name__)

SEND_TIMEOUT = 10   # seconds, per network wait

SECURITY_STARTTLS = 'starttls'
SECURITY_SSL = 'ssl'
SECURITY_NONE = 'none'
# The usual port for each, which is also what the hint on the form says.
SECURITY_MODES = {
    SECURITY_STARTTLS: ('STARTTLS', 587),
    SECURITY_SSL: ('SSL/TLS', 465),
    SECURITY_NONE: ('None (unencrypted)', 25),
}


class MailError(Exception):
    """A send that failed, with a message fit to show the person who tried."""


class MailConfig:
    """The SMTP settings as one object, read once."""

    def __init__(self, host, port, security, username, password, from_address,
                 from_name, verify_tls):
        self.host = (host or '').strip()
        self.port = port
        self.security = security if security in SECURITY_MODES else SECURITY_STARTTLS
        self.username = (username or '').strip()
        self.password = password or ''
        self.from_address = (from_address or '').strip()
        self.from_name = (from_name or '').strip()
        self.verify_tls = verify_tls

    @classmethod
    def from_settings(cls):
        return cls(
            host=app_settings.get('smtp_host'),
            port=app_settings.get('smtp_port'),
            security=app_settings.get('smtp_security'),
            username=app_settings.get('smtp_username'),
            password=app_settings.get('smtp_password'),
            from_address=app_settings.get('smtp_from_address'),
            from_name=app_settings.get('smtp_from_name'),
            verify_tls=app_settings.get('smtp_verify_tls'),
        )

    @property
    def is_configured(self):
        return bool(self.host and self.from_address)

    def __repr__(self):
        # Never the password: a config object ends up in tracebacks and logs.
        return (f'<MailConfig {self.security}://{self.host}:{self.port} '
                f'user={self.username or "-"} from={self.from_address}>')


def _tls_context(config):
    context = ssl.create_default_context()
    if not config.verify_tls:
        # For a relay on your own network with a self-signed certificate. The
        # connection is still encrypted; it is the server's identity that goes
        # unchecked, which is why it is off only when someone turns it off.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _connect(config):
    if config.security == SECURITY_SSL:
        return smtplib.SMTP_SSL(config.host, config.port, timeout=SEND_TIMEOUT,
                                context=_tls_context(config))
    server = smtplib.SMTP(config.host, config.port, timeout=SEND_TIMEOUT)
    if config.security == SECURITY_STARTTLS:
        server.ehlo()
        server.starttls(context=_tls_context(config))
        server.ehlo()
    return server


def build_message(config, to, subject, body):
    message = EmailMessage()
    # formataddr quotes a display name with a comma or other specials in it;
    # EmailMessage itself refuses a header containing a line break, so neither
    # field can be used to inject headers.
    message['From'] = formataddr((config.from_name, config.from_address)) \
        if config.from_name else config.from_address
    message['To'] = to
    message['Subject'] = subject
    message['Message-ID'] = make_msgid(domain=config.from_address.rpartition('@')[2] or None)
    message.set_content(body)
    return message


def send_mail(to, subject, body, config=None):
    """Send one plain-text email, or raise MailError saying why it did not go."""
    config = config or MailConfig.from_settings()
    if not config.host:
        raise MailError('No SMTP server is set. Enter one and save first.')
    if not config.from_address:
        raise MailError('No "From" address is set. Enter one and save first.')

    try:
        message = build_message(config, to, subject, body)
    except ValueError as error:
        raise MailError(f'That message could not be built: {error}.') from error

    try:
        server = _connect(config)
        try:
            # Only with a username: a relay that accepts mail from its own
            # network needs no login, and many refuse AUTH when offered one.
            if config.username:
                server.login(config.username, config.password)
            server.send_message(message)
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass
    except Exception as error:   # noqa: BLE001 — every failure becomes a sentence
        log.warning('Sending mail via %r failed: %s', config, error)
        raise MailError(explain(error, config)) from error


def _port_hint(config):
    """A nudge when the port and the Security setting look mismatched — the
    commonest mistake, and one whose raw error never mentions ports."""
    if config.port == 465 and config.security != SECURITY_SSL:
        return ' Port 465 usually needs SSL/TLS.'
    if config.port == 587 and config.security == SECURITY_SSL:
        return ' Port 587 usually means STARTTLS, not SSL/TLS.'
    return ''


def explain(error, config):
    """What went wrong, in terms of what to change on the form."""
    where = f'{config.host}:{config.port}'

    if isinstance(error, smtplib.SMTPAuthenticationError):
        return ('The server rejected the username or password. Some providers '
                '(Gmail, Outlook) need an app password rather than your normal one.')
    if isinstance(error, smtplib.SMTPNotSupportedError):
        if 'auth' in str(error).lower():
            return (f'{where} does not offer a login on this connection. Many servers '
                    'only allow one after STARTTLS; or clear the username if it is a '
                    'relay that needs none.')
        return (f'{where} does not offer STARTTLS. Try SSL/TLS (usually port 465), '
                'or None if it is a relay on your own network.')
    if isinstance(error, smtplib.SMTPSenderRefused):
        return (f'The server refused to send from {config.from_address}. Many '
                'providers only allow the address that belongs to the account you sign in with.')
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return 'The server refused the recipient address.'
    if isinstance(error, ssl.SSLCertVerificationError):
        return (f'The certificate presented by {config.host} could not be verified '
                f'({error.verify_message}). Check the server name, or turn off '
                'certificate checking if it is a relay on your own network.')
    if isinstance(error, ssl.SSLError):
        return (f'The secure connection to {where} failed.{_port_hint(config)} '
                'Check the Security setting.')
    # smtplib turns a timeout while waiting for the server's greeting into
    # "connection unexpectedly closed", which sends people looking for a
    # firewall dropping them. The timeout is still there as the cause.
    if isinstance(error, (socket.timeout, TimeoutError)) or (
            isinstance(error, smtplib.SMTPServerDisconnected)
            and isinstance(error.__context__, (socket.timeout, TimeoutError))):
        return (f'{where} did not answer within {SEND_TIMEOUT} seconds.{_port_hint(config)} '
                'Check the server name and port, and that this machine is allowed to reach it.')
    if isinstance(error, socket.gaierror):
        return f'The server name {config.host} could not be found.'
    if isinstance(error, ConnectionRefusedError):
        return f'{where} refused the connection. Check the port.'
    if isinstance(error, smtplib.SMTPServerDisconnected):
        return f'{where} closed the connection unexpectedly.{_port_hint(config)}'
    if isinstance(error, smtplib.SMTPResponseException):
        detail = error.smtp_error.decode(errors='replace') if isinstance(
            error.smtp_error, bytes) else str(error.smtp_error)
        return f'The server answered {error.smtp_code}: {detail}'
    if isinstance(error, OSError):
        return f'Could not connect to {where}: {error.strerror or error}.'
    return f'Sending failed: {error}'


def send_test_email(to, sent_by):
    """The Settings page's test button. Uses the saved settings, so a test that
    works is proof of exactly what notifications will use."""
    send_mail(
        to,
        'Home CMMS test email',
        f'This is a test email from Home CMMS, sent by {sent_by}.\n\n'
        'If you are reading it, the email settings work.\n',
    )
