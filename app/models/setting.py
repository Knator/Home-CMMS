"""In-app preferences, stored in the database rather than in the environment.

The environment describes the *deployment* — where the database lives, what port
to listen on, whether there is a proxy in front. Those belong in .env because
they are set once by whoever runs the instance and are needed before the app can
start. Preferences are different: they are chosen by an administrator while the
app is running, and they should not require editing a file and restarting a
container.

Rewriting .env from the application was considered and rejected. It is a
deployment artifact the operator owns and may have mounted read-only; writing to
it races with their own edits, loses their comments and ordering, and in a
container it is often not even the file the settings came from. Immich, Nextcloud
and Home Assistant all take the same route this does — bootstrap from the
environment, keep runtime preferences in their own storage.

Where both exist, the environment wins and the field is shown as locked. Setting
a variable is a deliberate act by whoever runs the server, and a web form should
not silently override it.
"""
from app.extensions import db
from app.utils import utcnow


class Setting(db.Model):
    __tablename__ = 'settings'

    key = db.Column(db.String(64), primary_key=True)
    # Stored as text and parsed by the accessors in app/settings.py, so a new
    # preference needs no migration — only an entry in DEFAULTS.
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    def __repr__(self):
        return f'<Setting {self.key}={self.value!r}>'
