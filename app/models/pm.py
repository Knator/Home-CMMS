from datetime import datetime, date, timedelta
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    location = db.relationship('Location', foreign_keys=[location_id], backref='pms')
    creator = db.relationship('User', foreign_keys=[created_by])
    generated_work_orders = db.relationship('WorkOrder', backref='source_pm', lazy='dynamic', foreign_keys='WorkOrder.pm_id')

    def advance_schedule(self):
        today = date.today()
        self.last_generated_date = today
        self.next_due_date = today + timedelta(days=self.interval_days)

    @property
    def is_overdue(self):
        return self.is_active and self.next_due_date < date.today()

    def __repr__(self):
        return f'<PM {self.name}>'
