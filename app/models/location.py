from app.utils import utcnow
from app.extensions import db


class Location(db.Model):
    __tablename__ = 'locations'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    assets = db.relationship('Asset', backref='location', lazy='dynamic')
    work_orders = db.relationship('WorkOrder', backref='location', lazy='dynamic')

    def __repr__(self):
        return f'<Location {self.name}>'
