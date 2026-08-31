from datetime import date, timedelta
from app.utils import utcnow
from app.extensions import db


class PM(db.Model):
    __tablename__ = 'pms'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    asset_id = db.Column(db.Integer, db.ForeignKey('assets.id'), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)
    job_plan_id = db.Column(db.Integer, db.ForeignKey('job_plans.id'), nullable=True)
    interval_days = db.Column(db.Integer, nullable=False)
    # Generate the work order this many days before it falls due, so there is
    # time to prepare. Must stay below interval_days or the next occurrence
    # would come due for generation again immediately.
    generate_lead_days = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    # Days past the due date before it counts as overdue.
    overdue_grace_days = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    next_due_date = db.Column(db.Date, nullable=False)
    last_generated_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # False (default): fixed schedule — the next due date is anchored to the
    # previous due date, so the PM keeps its calendar rhythm regardless of when
    # the work actually happened.
    # True: floating schedule — the next due date is measured from when the work
    # was actually completed, so a job done late pushes the whole cycle out.
    schedule_from_completion = db.Column(db.Boolean, nullable=False,
                                         default=False, server_default='0')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    location = db.relationship('Location', foreign_keys=[location_id], backref='pms')
    creator = db.relationship('User', foreign_keys=[created_by])
    generated_work_orders = db.relationship('WorkOrder', backref='source_pm', lazy='dynamic', foreign_keys='WorkOrder.pm_id')

    def advance_schedule(self, on_date=None):
        """Move the schedule to the next occurrence after `on_date`.

        The next due date is anchored to the previous due date, not to the day
        this happened to run, so a late scheduler tick or a manual "Generate WO
        Now" does not permanently shift every future occurrence. If the PM is
        several intervals overdue (the app was off for a while), whole intervals
        are skipped so exactly one work order is generated to catch up.
        """
        today = on_date or date.today()
        interval = timedelta(days=max(self.interval_days or 1, 1))
        self.last_generated_date = today

        next_due = (self.next_due_date or today) + interval
        while next_due <= today:
            next_due += interval
        self.next_due_date = next_due

    @property
    def generation_date(self):
        """The day this PM starts generating: its due date, minus the lead."""
        return self.next_due_date - timedelta(days=self.generate_lead_days or 0)

    def is_due_for_generation(self, on_date=None):
        today = on_date or date.today()
        return bool(self.is_active) and self.generation_date <= today

    @property
    def overdue_from(self):
        """First day this PM counts as overdue."""
        return self.next_due_date + timedelta(days=(self.overdue_grace_days or 0) + 1)

    def last_completion_date(self):
        """Most recent completion among the work orders this PM generated."""
        from app.models.work_order import WorkOrder

        return (
            db.session.query(db.func.max(WorkOrder.completed_date))
            .filter(WorkOrder.pm_id == self.id, WorkOrder.completed_date.isnot(None))
            .scalar()
        )

    def reschedule_from_completion(self):
        """Re-anchor the next due date to the latest completion.

        Only meaningful for a floating schedule. Uses the newest completion
        across all of this PM's work orders rather than whichever one was just
        saved, so completing them out of order cannot drag the schedule
        backwards, and repeating the calculation changes nothing.

        Returns True if the due date moved.
        """
        if not self.schedule_from_completion:
            return False

        completed = self.last_completion_date()
        if completed is None:
            return False

        next_due = completed + timedelta(days=max(self.interval_days or 1, 1))
        if next_due == self.next_due_date:
            return False
        self.next_due_date = next_due
        return True

    @property
    def schedule_basis(self):
        return 'Last completion' if self.schedule_from_completion else 'Fixed interval'

    @property
    def is_overdue(self):
        return bool(self.is_active) and date.today() >= self.overdue_from

    def __repr__(self):
        return f'<PM {self.name}>'
