from app.extensions import db
from app.utils import utcnow


class AuthAttempt(db.Model):
    """One sign-in or API-token attempt.

    Kept in the database rather than in process memory so a lockout survives a
    restart — an in-memory counter is trivially reset by crashing the app, and
    would not be shared if this ever ran with more than one worker. It doubles
    as the audit trail for failed sign-ins.
    """
    __tablename__ = 'auth_attempts'

    id = db.Column(db.Integer, primary_key=True)
    # Username for a form login, or 'api' for a token attempt. Never a password.
    identifier = db.Column(db.String(120), nullable=False, index=True)
    ip_address = db.Column(db.String(64), index=True)
    successful = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    def __repr__(self):
        return f'<AuthAttempt {self.identifier} ok={self.successful}>'
