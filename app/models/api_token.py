import hashlib
import secrets

from app.extensions import db
from app.utils import utcnow

TOKEN_BYTES = 32
MAX_NAME = 80


class ApiToken(db.Model):
    """A named bearer token.

    A user may hold several — one per integration — so a token can be revoked
    for a single consumer without disturbing the others, and the name records
    where it is being used.
    """
    __tablename__ = 'api_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    name = db.Column(db.String(MAX_NAME), nullable=False)
    # SHA-256 of the token. Tokens are high-entropy random strings, so a fast
    # digest is enough — unlike a password there is nothing to brute force —
    # and storing only the hash means a database leak yields no live credential.
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    last_used_at = db.Column(db.DateTime)

    user = db.relationship('User', backref=db.backref(
        'api_tokens', lazy='dynamic', cascade='all, delete-orphan',
        order_by='ApiToken.created_at.desc()'))

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def issue(cls, user, name):
        """Create a token, store only its hash, and return (record, plaintext).

        The plaintext is returned once and never persisted; the caller must show
        it immediately.
        """
        plaintext = secrets.token_urlsafe(TOKEN_BYTES)
        token = cls(user_id=user.id, name=name.strip()[:MAX_NAME],
                    token_hash=cls.hash_token(plaintext))
        db.session.add(token)
        return token, plaintext

    def __repr__(self):
        return f'<ApiToken {self.name!r} user={self.user_id}>'
