from app.utils import utcnow
from app.extensions import db
from app.models.mixins import (
    ItemFieldsMixin, ITEM_MATERIAL, ITEM_TOOL, ITEM_KINDS,
)


class JobPlanTask(db.Model):
    __tablename__ = 'job_plan_tasks'

    id = db.Column(db.Integer, primary_key=True)
    job_plan_id = db.Column(db.Integer, db.ForeignKey('job_plans.id', ondelete='CASCADE'), nullable=False)
    sequence = db.Column(db.Integer, nullable=False, default=1)
    description = db.Column(db.Text, nullable=False)
    estimated_minutes = db.Column(db.Integer)


class JobPlanItem(ItemFieldsMixin, db.Model):
    """A required material or tool.

    Materials and tools are structurally identical — an ordered line with a
    description and an optional quantity — so they share one table with a `kind`
    discriminator rather than two near-duplicate models and migrations.
    """
    __tablename__ = 'job_plan_items'

    id = db.Column(db.Integer, primary_key=True)
    job_plan_id = db.Column(db.Integer, db.ForeignKey('job_plans.id', ondelete='CASCADE'),
                            nullable=False, index=True)

    def __repr__(self):
        return f'<JobPlanItem {self.kind} {self.description[:30]}>'


class JobPlan(db.Model):
    __tablename__ = 'job_plans'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))

    tasks = db.relationship(
        'JobPlanTask',
        backref='job_plan',
        lazy='dynamic',
        cascade='all, delete-orphan',
        order_by='JobPlanTask.sequence',
    )
    items = db.relationship(
        'JobPlanItem',
        backref='job_plan',
        lazy='dynamic',
        cascade='all, delete-orphan',
        order_by='JobPlanItem.sequence',
    )
    creator = db.relationship('User', foreign_keys=[created_by])
    work_orders = db.relationship('WorkOrder', backref='job_plan', lazy='dynamic')
    pms = db.relationship('PM', backref='job_plan', lazy='dynamic')

    @property
    def materials(self):
        return self.items.filter_by(kind=ITEM_MATERIAL).all()

    @property
    def tools(self):
        return self.items.filter_by(kind=ITEM_TOOL).all()

    @property
    def total_minutes(self):
        """Sum of the tasks' estimates; tasks with no estimate count as zero."""
        return sum(t.estimated_minutes or 0 for t in self.tasks)

    def __repr__(self):
        return f'<JobPlan {self.name}>'
