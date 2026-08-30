from app.utils import utcnow
from app.extensions import db

ENTITY_TYPES = ['location', 'asset', 'work_order', 'job_plan', 'pm']


class Attachment(db.Model):
    __tablename__ = 'attachments'

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    stored_filename = db.Column(db.String(300), nullable=False)
    original_filename = db.Column(db.String(300), nullable=False)
    # Optional human label, e.g. "Furnace manual" for 7bf2_MAN-4471-rev-c.pdf.
    display_name = db.Column(db.String(255))
    file_size = db.Column(db.Integer)
    mime_type = db.Column(db.String(100))
    uploaded_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    uploaded_at = db.Column(db.DateTime, default=utcnow)

    uploader = db.relationship('User', backref='uploads')

    @property
    def label(self):
        """What to show in the UI: the friendly name if set, else the filename."""
        return self.display_name or self.original_filename

    @property
    def extension(self):
        _, _, ext = self.original_filename.rpartition('.')
        return ext.lower() if ext else ''

    @property
    def download_name(self):
        """Friendly name for the saved file, keeping the original extension.

        A label like "Furnace manual" must still land on disk as a .pdf, or the
        browser and OS won't know how to open it.
        """
        if not self.display_name:
            return self.original_filename
        ext = self.extension
        if ext and not self.display_name.lower().endswith('.' + ext):
            return f'{self.display_name}.{ext}'
        return self.display_name

    def __repr__(self):
        return f'<Attachment {self.original_filename}>'
