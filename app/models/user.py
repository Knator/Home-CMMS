from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db
from app.utils import utcnow

# Most privileged first, which is also the order the roles sort and are offered
# in. Each includes everything below it: a super admin is an admin too.
ROLE_SUPER_ADMIN = 'super_admin'
ROLE_ADMIN = 'admin'
ROLE_USER = 'user'
ROLES = (ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER)
ADMIN_ROLES = (ROLE_SUPER_ADMIN, ROLE_ADMIN)
ROLE_LABELS = {ROLE_SUPER_ADMIN: 'Super Admin', ROLE_ADMIN: 'Admin', ROLE_USER: 'User'}


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
    role = db.Column(db.String(20), nullable=False, default='user')  # one of ROLES
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
        """Users and settings. True for a super admin as well."""
        return self.role in ADMIN_ROLES

    @property
    def is_super_admin(self):
        """Maintenance — backups, restore, the database, the sign-in log —
        and the only role that can make or unmake another super admin."""
        return self.role == ROLE_SUPER_ADMIN

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def assignable_roles(self):
        """The roles this user may give someone. Never one above their own: an
        admin who could grant super admin could grant it to themselves, and the
        role would mean nothing."""
        if self.is_super_admin:
            return ROLES
        if self.is_admin:
            return (ROLE_ADMIN, ROLE_USER)
        return ()

    def outranks(self, other):
        """True when `other` is a super admin and this user is not — the one
        case where an admin may not touch an account."""
        return other.is_super_admin and not self.is_super_admin

    def __repr__(self):
        return f'<User {self.username}>'
