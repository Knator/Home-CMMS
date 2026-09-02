from app.extensions import db
from app.models.mixins import ItemFieldsMixin


class WorkOrderItem(ItemFieldsMixin, db.Model):
    """A material or tool on one work order.

    Copied from the job plan when the work order is raised, then editable — and
    a work order can carry items with no job plan at all. It is a snapshot, not
    a live reference: what a job plan calls for today is not necessarily what a
    job done six months ago actually used.
    """
    __tablename__ = 'work_order_items'

    id = db.Column(db.Integer, primary_key=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_orders.id', ondelete='CASCADE'),
                              nullable=False, index=True)

    def __repr__(self):
        return f'<WorkOrderItem {self.kind} {self.description[:30]}>'
