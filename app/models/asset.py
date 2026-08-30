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
    __table_args__ = (
        db.Index('uq_assets_asset_number', 'asset_number', unique=True),
    )

    id = db.Column(db.Integer, primary_key=True)
    asset_number = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('assets.id'), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE,
                       server_default=STATUS_ACTIVE)
    # Optional photo. ON DELETE SET NULL so removing the underlying attachment
    # (or purging the asset's files) cannot leave a dangling reference.
    image_attachment_id = db.Column(
        db.Integer,
        db.ForeignKey('attachments.id', ondelete='SET NULL', name='fk_assets_image_attachment'),
        nullable=True,
    )
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
    image = db.relationship('Attachment', foreign_keys=[image_attachment_id])
    work_orders = db.relationship('WorkOrder', backref='asset', lazy='dynamic',
                                  order_by='WorkOrder.created_at.desc()')
    pms = db.relationship('PM', backref='asset', lazy='dynamic')

    @staticmethod
    def generate_asset_number():
        """Next AST-NNNNN.

        Read-then-write like work order numbers, so the unique index is the real
        guard; create_asset() in app/services.py retries on collision. Assets are
        long-lived, so the sequence is not year-scoped.
        """
        last = (
            Asset.query
            .filter(Asset.asset_number.like('AST-%'))
            .order_by(Asset.asset_number.desc())
            .first()
        )
        seq = 1
        if last:
            try:
                seq = int(last.asset_number.rsplit('-', 1)[-1]) + 1
            except ValueError:
                seq = 1
        return f'AST-{seq:05d}'

    @property
    def display_label(self):
        """Name plus number, for pickers where two assets may share a name."""
        return f'{self.name} ({self.asset_number})'

    def __repr__(self):
        return f'<Asset {self.asset_number} {self.name}>'


ASSET_STATUSES = LIFECYCLE_STATUSES
