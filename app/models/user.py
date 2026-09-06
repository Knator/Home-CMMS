from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db
from app.utils import utcnow


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    # What people are called on screen, as distinct from what they type to sign
    # in. Left NULL rather than seeded with the username, so it means "not set"
    # and keeps following the username if that is ever changed; a copy taken at
    # creation would silently go stale. Deliberately not unique: two Alexes are
    # allowed, because this never addresses anybody — the username does.
    display_name = db.Column(db.String(120))
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')  # 'admin' | 'user'
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def label(self):
        """What to show wherever this user is named. Mirrors Attachment.label:
        the friendly name if there is one, otherwise the underlying identity."""
        return self.display_name or self.username

    @property
    def has_api_token(self):
        return self.api_tokens.count() > 0

    @property
    def is_admin(self):
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.username}>'
