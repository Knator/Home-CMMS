import hashlib
import secrets

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db
from app.utils import utcnow

API_TOKEN_BYTES = 32


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')  # 'admin' | 'user'
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    last_login = db.Column(db.DateTime)
    # SHA-256 of the bearer token. Tokens are high-entropy random strings, so a
    # fast digest is enough — unlike a password, there is nothing to brute force.
    # Stored hashed so a database leak does not hand over live credentials.
    api_token_hash = db.Column(db.String(64), index=True)
    api_token_created = db.Column(db.DateTime)
    api_token_last_used = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @staticmethod
    def hash_api_token(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def issue_api_token(self):
        """Generate a token, store only its hash, and return it once.

        The caller must show it to the user immediately — it cannot be recovered.
        """
        token = secrets.token_urlsafe(API_TOKEN_BYTES)
        self.api_token_hash = self.hash_api_token(token)
        self.api_token_created = utcnow()
        self.api_token_last_used = None
        return token

    def revoke_api_token(self):
        self.api_token_hash = None
        self.api_token_created = None
        self.api_token_last_used = None

    @property
    def has_api_token(self):
        return bool(self.api_token_hash)

    @property
    def is_admin(self):
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.username}>'
