from datetime import datetime
from app.extensions import db


class JobPlanTask(db.Model):
    __tablename__ = 'job_plan_tasks'

    id = db.Column(db.Integer, primary_key=True)
    job_plan_id = db.Column(db.Integer, db.ForeignKey('job_plans.id', ondelete='CASCADE'), nullable=False)
    sequence = db.Column(db.Integer, nullable=False, default=1)
    description = db.Column(db.Text, nullable=False)
    estimated_minutes = db.Column(db.Integer)


class JobPlan(db.Model):
    __tablename__ = 'job_plans'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    tasks = db.relationship(
        'JobPlanTask',
        backref='job_plan',
        lazy='dynamic',
        cascade='all, delete-orphan',
        order_by='JobPlanTask.sequence',
    )
    creator = db.relationship('User', foreign_keys=[created_by])
    work_orders = db.relationship('WorkOrder', backref='job_plan', lazy='dynamic')
    pms = db.relationship('PM', backref='job_plan', lazy='dynamic')

    def __repr__(self):
        return f'<JobPlan {self.name}>'
