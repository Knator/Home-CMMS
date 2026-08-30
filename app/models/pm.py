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
    next_due_date = db.Column(db.Date, nullable=False)
    last_generated_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
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
    def is_overdue(self):
        return self.is_active and self.next_due_date < date.today()

    def __repr__(self):
        return f'<PM {self.name}>'
