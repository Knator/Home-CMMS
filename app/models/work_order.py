from datetime import date, timedelta

from sqlalchemy.orm import validates

from app.utils import to_local, utcnow
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

    # Set when the work order is archived. The foreign keys above are kept, so
    # the asset is still reachable and still protected from deletion — but
    # everything this record *displays* comes from the snapshot, so renaming an
    # asset or a location later cannot rewrite history. Same reasoning as
    # WorkOrderItem being a copy rather than a live view of the job plan.
    # When the status last changed — Maximo's statusdate. Needed because a
    # cancelled work order otherwise carries no date at all: completed_date is
    # only set on completion, and updated_at moves whenever anything is edited,
    # so adding a note would reset an auto-archive clock built on it.
    status_changed_at = db.Column(db.DateTime)
    archived_at = db.Column(db.DateTime)
    archived_snapshot = db.Column(db.JSON)

    @validates('status')
    def _stamp_status_change(self, _key, value):
        """Stamp every status change, wherever it is made.

        A validator rather than a line in each route: status is set from the
        create and edit forms, the API, the PM generator and the tests, and one
        of those would eventually be missed.
        """
        if value != self.status:
            self.status_changed_at = utcnow()
        return value

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
    def closed_on(self):
        """The local calendar date this work order stopped being live.

        completed_date first: it is the date the work was actually done, is
        user-editable, and is what someone means by "completed on". Cancelled
        work has no such field, so it falls back to when the status changed.
        """
        if self.completed_date:
            return self.completed_date
        if self.status_changed_at:
            # Stored UTC, compared against a local calendar date like every
            # other Date in the schema.
            return to_local(self.status_changed_at).date()
        return None

    @property
    def is_archived(self):
        """Archiving is a flag, not a status — the Maximo history flag, not a
        sixth value in the status list.

        Keeping them separate is what preserves the outcome: an archived work
        order still says whether it was completed or cancelled, which a status
        of 'archived' would have overwritten. archived_at is the flag and the
        timestamp at once, so there is one source of truth rather than a boolean
        that can disagree with a date.
        """
        return self.archived_at is not None

    # Work that is finished with, one way or the other. An open or on-hold work
    # order still has changes coming, so archiving it would freeze a record
    # mid-flight.
    ARCHIVABLE_FROM = ('completed', 'cancelled')

    @property
    def can_be_archived(self):
        return self.status in self.ARCHIVABLE_FROM

    def snapshot_value(self, key, default=None):
        """A frozen display value, or None when this is not archived."""
        return (self.archived_snapshot or {}).get(key, default)

    @property
    def status_class(self):
        return STATUS_COLORS.get(self.status, '')

    @property
    def priority_class(self):
        return PRIORITY_COLORS.get(self.priority, '')

    def __repr__(self):
        return f'<WorkOrder {self.wo_number}>'
