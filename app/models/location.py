from app.utils import utcnow
from app.extensions import db
from app.models.mixins import (
    HierarchyMixin, LifecycleMixin, LIFECYCLE_STATUSES, STATUS_ACTIVE,
)


class Location(LifecycleMixin, HierarchyMixin, db.Model):
    __tablename__ = 'locations'
    __table_args__ = (
        # Names are unique among siblings, not globally, so "Basement > Bathroom"
        # and "Main Floor > Bathroom" can coexist.
        #
        # COALESCE is required: SQL treats NULLs as distinct, so a plain
        # UNIQUE(parent_id, name) would happily allow two top-level locations
        # with the same name. lower() makes the rule case-insensitive, matching
        # what the form checks.
        db.Index(
            'uq_locations_parent_name',
            db.func.coalesce(db.text('parent_id'), db.text('-1')),
            db.func.lower(db.text('name')),
            unique=True,
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE,
                       server_default=STATUS_ACTIVE)
    description = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # remote_side points at the "one" end of the self-join, so SQLAlchemy knows
    # parent_id -> id rather than the other way round.
    parent = db.relationship(
        'Location', remote_side=[id], backref=db.backref('children', order_by='Location.name'),
    )
    assets = db.relationship('Asset', backref='location', lazy='dynamic')
    work_orders = db.relationship('WorkOrder', backref='location', lazy='dynamic')

    def __repr__(self):
        return f'<Location {self.name}>'


LOCATION_STATUSES = LIFECYCLE_STATUSES
