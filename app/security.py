"""Brute-force protection for sign-in and API tokens.

Two independent limits, because they stop different attacks:

  * per identifier — someone guessing one account's password
  * per source address — someone spraying many accounts, or guessing tokens,
    where no single identifier accumulates enough failures to trip the first

Both are recorded in the database, so a lockout is not cleared by restarting the
application and the failures remain visible afterwards.
"""
from datetime import timedelta

from flask import request

from app.extensions import db
from app.models.auth_attempt import AuthAttempt
from app.utils import utcnow

# Enough headroom for someone genuinely fumbling a password, far too little for
# a guessing run.
MAX_IDENTIFIER_FAILURES = 5
MAX_IP_FAILURES = 20
WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)

# Failed attempts are the audit trail; they do not need keeping forever.
RETENTION = timedelta(days=30)

API_IDENTIFIER = 'api-token'


def client_ip():
    """Best-effort source address.

    Behind a reverse proxy every request appears to come from the proxy unless
    the app is told to trust forwarded headers, which would make the per-address
    limit useless — see the deployment notes.
    """
    return (request.remote_addr or 'unknown')[:64]


def _failures_since(cutoff, identifier=None, ip_address=None):
    query = AuthAttempt.query.filter(
        AuthAttempt.successful.is_(False),
        AuthAttempt.created_at >= cutoff,
    )
    if identifier is not None:
        query = query.filter(AuthAttempt.identifier == identifier)
    if ip_address is not None:
        query = query.filter(AuthAttempt.ip_address == ip_address)
    return query.count()


def lockout_remaining(identifier=None, ip_address=None):
    """Seconds until the caller may try again, or 0 if they may try now."""
    now = utcnow()
    cutoff = now - WINDOW

    checks = []
    if identifier:
        checks.append((identifier, None, MAX_IDENTIFIER_FAILURES))
    if ip_address:
        checks.append((None, ip_address, MAX_IP_FAILURES))

    longest = 0
    for ident, ip, limit in checks:
        if _failures_since(cutoff, ident, ip) < limit:
            continue
        latest = (
            AuthAttempt.query
            .filter(AuthAttempt.successful.is_(False),
                    AuthAttempt.created_at >= cutoff)
            .filter(AuthAttempt.identifier == ident if ident else AuthAttempt.ip_address == ip)
            .order_by(AuthAttempt.created_at.desc())
            .first()
        )
        if latest is None:
            continue
        unlock_at = latest.created_at + LOCKOUT
        longest = max(longest, int((unlock_at - now).total_seconds()))
    return max(longest, 0)


def is_locked_out(identifier=None, ip_address=None):
    return lockout_remaining(identifier, ip_address) > 0


def record_attempt(identifier, successful, ip_address=None):
    """Log an attempt. A success clears that identifier's recent failures."""
    ip_address = ip_address or client_ip()
    db.session.add(AuthAttempt(identifier=identifier[:120], ip_address=ip_address,
                               successful=successful))

    if successful:
        # Otherwise a legitimate sign-in after four typos would still be one
        # mistake away from locking the account out.
        AuthAttempt.query.filter(
            AuthAttempt.identifier == identifier[:120],
            AuthAttempt.successful.is_(False),
        ).delete(synchronize_session=False)
        prune_attempts()

    db.session.commit()


def prune_attempts():
    """Drop records past the retention window."""
    return AuthAttempt.query.filter(
        AuthAttempt.created_at < utcnow() - RETENTION
    ).delete(synchronize_session=False)


def clear_lockouts():
    """Admin escape hatch: forget every failure, releasing all lockouts."""
    removed = AuthAttempt.query.filter(AuthAttempt.successful.is_(False)).delete(
        synchronize_session=False)
    db.session.commit()
    return removed


# Just enough on the maintenance page to notice something is happening. The
# full log lives on its own page, so a busy day of failures cannot bury the
# backup controls underneath it.
CARD_FAILURE_LIMIT = 5
PAGE_SIZE = 50


def recent_failures(limit=CARD_FAILURE_LIMIT):
    return (
        AuthAttempt.query
        .filter(AuthAttempt.successful.is_(False))
        .order_by(AuthAttempt.created_at.desc())
        .limit(limit)
        .all()
    )


def count_failures():
    return AuthAttempt.query.filter(AuthAttempt.successful.is_(False)).count()


def attempt_page(page=1, only_failures=True, per_page=PAGE_SIZE):
    """A page of the attempt log, newest first."""
    query = AuthAttempt.query
    if only_failures:
        query = query.filter(AuthAttempt.successful.is_(False))
    return (
        query.order_by(AuthAttempt.created_at.desc())
        .paginate(page=max(page, 1), per_page=per_page, error_out=False)
    )
