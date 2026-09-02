from datetime import date, timedelta
from app.utils import utcnow
from app.extensions import db

WO_STATUSES = ['open', 'in_progress', 'on_hold', 'completed', 'cancelled']
WO_PRIORITIES = ['low', 'medium', 'high', 'critical']
WO_TYPES = ['planned', 'unplanned']

STATUS_COLORS = {
    'open': 'status-open',
    'in_progress': 'status-in-progress',
    'on_hold': 'status-on-hold',
    'completed': 'status-completed',
    'cancelled': 'status-cancelled',
}

PRIORITY_COLORS = {
    'low': 'priority-low',
    'medium': 'priority-medium',
    'high': 'priority-high',
    'critical': 'priority-critical',
}


class WorkOrder(db.Model):
    __tablename__ = 'work_orders'

    id = db.Column(db.Integer, primary_key=True)
    wo_number = db.Column(db.String(20), unique=True, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    wo_type = db.Column(db.String(20), nullable=False, default='unplanned')
    status = db.Column(db.String(20), nullable=False, default='open')
    priority = db.Column(db.String(20), nullable=False, default='medium')
    asset_id = db.Column(db.Integer, db.ForeignKey('assets.id'), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)
    job_plan_id = db.Column(db.Integer, db.ForeignKey('job_plans.id'), nullable=True)
    pm_id = db.Column(db.Integer, db.ForeignKey('pms.id'), nullable=True)
    assigned_to = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    # Days past the due date before this counts as overdue. Copied from the PM
    # when the work order is generated from one.
    overdue_grace_days = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    completed_date = db.Column(db.Date, nullable=True)
    description = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    items = db.relationship(
        'WorkOrderItem', backref='work_order', lazy='dynamic',
        cascade='all, delete-orphan', order_by='WorkOrderItem.sequence',
    )
    assignee = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_work_orders')
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_work_orders')

    @staticmethod
    def generate_wo_number():
        """Next WO-YYYY-NNNNN for the current year.

        This is a read-then-write, so two concurrent creates can pick the same
        number. The unique constraint catches that; create_work_order() in
        app/services.py retries. Do not insert a work order without it.
        """
        year = date.today().year
        prefix = f"WO-{year}-"
        last = (
            WorkOrder.query
            .filter(WorkOrder.wo_number.like(f"{prefix}%"))
            .order_by(WorkOrder.wo_number.desc())
            .first()
        )
        seq = 1
        if last:
            try:
                seq = int(last.wo_number.rsplit('-', 1)[-1]) + 1
            except ValueError:
                seq = 1
        return f"{prefix}{seq:05d}"

    @property
    def materials(self):
        from app.models.mixins import ITEM_MATERIAL
        return self.items.filter_by(kind=ITEM_MATERIAL).all()

    @property
    def tools(self):
        from app.models.mixins import ITEM_TOOL
        return self.items.filter_by(kind=ITEM_TOOL).all()

    @property
    def overdue_from(self):
        """First day this work order counts as overdue, or None if never."""
        if self.due_date is None:
            return None
        return self.due_date + timedelta(days=(self.overdue_grace_days or 0) + 1)

    @property
    def is_overdue(self):
        return (
            self.overdue_from is not None and
            self.status not in ('completed', 'cancelled') and
            date.today() >= self.overdue_from
        )

    @property
    def status_class(self):
        return STATUS_COLORS.get(self.status, '')

    @property
    def priority_class(self):
        return PRIORITY_COLORS.get(self.priority, '')

    def __repr__(self):
        return f'<WorkOrder {self.wo_number}>'
