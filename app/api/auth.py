"""Bearer-token authentication for the API.

Session cookies are no use to a script, and adding an unauthenticated write
endpoint to an app that may be reachable on a LAN is not acceptable — so every
API call carries a token belonging to a real, active user.
"""
import secrets
from functools import wraps

from flask import g, request

from app.api.errors import unauthorized
from app.extensions import db
from app.models.api_token import ApiToken
from app.utils import utcnow


def _presented_token():
    """Accept `Authorization: Bearer <token>` or `X-API-Key: <token>`."""
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer '):
        return header[7:].strip()
    return request.headers.get('X-API-Key', '').strip() or None


def authenticate():
    """Resolve a presented token to its owner, or None."""
    presented = _presented_token()
    if not presented:
        return None

    digest = ApiToken.hash_token(presented)
    # Indexed lookup on the digest, then a constant-time compare so a match
    # cannot be distinguished by timing.
    record = ApiToken.query.filter_by(token_hash=digest).first()
    if record is None or not secrets.compare_digest(record.token_hash, digest):
        return None
    if record.user is None or not record.user.is_active:
        return None

    record.last_used_at = utcnow()
    return record.user


def api_token_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = authenticate()
        if user is None:
            return unauthorized()
        g.api_user = user
        db.session.commit()      # persists the token's last-used stamp
        return view(*args, **kwargs)
    return wrapped
