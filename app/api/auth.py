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
from app.models.user import User
from app.utils import utcnow


def _presented_token():
    """Accept `Authorization: Bearer <token>` or `X-API-Key: <token>`."""
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer '):
        return header[7:].strip()
    return request.headers.get('X-API-Key', '').strip() or None


def authenticate():
    token = _presented_token()
    if not token:
        return None

    digest = User.hash_api_token(token)
    # Indexed lookup on the digest, then a constant-time compare so the match
    # itself cannot be timed.
    for user in User.query.filter_by(api_token_hash=digest).all():
        if user.is_active and secrets.compare_digest(user.api_token_hash, digest):
            return user
    return None


def api_token_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = authenticate()
        if user is None:
            return unauthorized()
        g.api_user = user
        user.api_token_last_used = utcnow()
        db.session.commit()
        return view(*args, **kwargs)
    return wrapped
