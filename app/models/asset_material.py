from app.extensions import db
from app.models.mixins import MAX_PART_NUMBER, MAX_QUANTITY
from app.utils import utcnow


class AssetMaterial(db.Model):
    """A part used on an asset, accumulated from completed work orders.

    The reason this table exists rather than querying work orders on demand: the
    point is to answer "what part did I use last time?" months later, and that
    answer must survive the work order being deleted or its job plan changing.
    Repeat uses update the same row instead of piling up duplicates.
    """
    __tablename__ = 'asset_materials'
    __table_args__ = (
        db.Index('ix_asset_materials_asset_id', 'asset_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('assets.id', ondelete='CASCADE'),
                         nullable=False)
    description = db.Column(db.Text, nullable=False)
    part_number = db.Column(db.String(MAX_PART_NUMBER))
    quantity = db.Column(db.String(MAX_QUANTITY))
    times_used = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    first_used_on = db.Column(db.Date)
    last_used_on = db.Column(db.Date)
    # Where it was last used. SET NULL so deleting that work order leaves the
    # part on record rather than taking it with it.
    last_work_order_id = db.Column(
        db.Integer,
        db.ForeignKey('work_orders.id', ondelete='SET NULL', name='fk_asset_materials_work_order'),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    asset = db.relationship('Asset', backref=db.backref(
        'materials', lazy='dynamic', cascade='all, delete-orphan',
        order_by='AssetMaterial.description'))
    last_work_order = db.relationship('WorkOrder', foreign_keys=[last_work_order_id])

    @property
    def normalised_part_number(self):
        return (self.part_number or '').strip().lower()

    @property
    def normalised_description(self):
        return (self.description or '').strip().lower()

    def __repr__(self):
        return f'<AssetMaterial {self.part_number or self.description[:30]}>'
