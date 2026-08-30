from app.utils import utcnow
from app.extensions import db
from app.models.mixins import (
    HierarchyMixin, LifecycleMixin, LIFECYCLE_STATUSES, STATUS_ACTIVE,
)

ASSET_CATEGORIES = [
    'HVAC', 'Plumbing', 'Electrical', 'Appliance',
    'Flooring', 'Roofing', 'Structural', 'Garage',
    'Networking', 'Security', 'Landscaping', 'Other',
]


class Asset(LifecycleMixin, HierarchyMixin, db.Model):
    __tablename__ = 'assets'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('assets.id'), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE,
                       server_default=STATUS_ACTIVE)
    category = db.Column(db.String(50))
    make = db.Column(db.String(100))
    model = db.Column(db.String(100))
    serial_number = db.Column(db.String(100))
    purchase_date = db.Column(db.Date)
    install_date = db.Column(db.Date)
    warranty_expiry = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    parent = db.relationship(
        'Asset', remote_side=[id], backref=db.backref('children', order_by='Asset.name'),
    )
    work_orders = db.relationship('WorkOrder', backref='asset', lazy='dynamic',
                                  order_by='WorkOrder.created_at.desc()')
    pms = db.relationship('PM', backref='asset', lazy='dynamic')

    def __repr__(self):
        return f'<Asset {self.name}>'


ASSET_STATUSES = LIFECYCLE_STATUSES
