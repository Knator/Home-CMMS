"""Shared lifecycle status and parent/child behaviour for Locations and Assets.

Modelled on Maximo: an asset or location that work has been recorded against is
never deleted, because that would orphan the history. It moves through a
lifecycle instead — operating, temporarily out of service, or retired.
"""

from app.extensions import db

STATUS_ACTIVE = 'active'
STATUS_INACTIVE = 'inactive'
STATUS_DECOMMISSIONED = 'decommissioned'

LIFECYCLE_STATUSES = [STATUS_ACTIVE, STATUS_INACTIVE, STATUS_DECOMMISSIONED]

STATUS_LABELS = {
    STATUS_ACTIVE: 'Active',
    STATUS_INACTIVE: 'Inactive',
    STATUS_DECOMMISSIONED: 'Decommissioned',
}

STATUS_HELP = {
    STATUS_ACTIVE: 'In service. Available for new work orders and PM schedules.',
    STATUS_INACTIVE: 'Temporarily out of service. Existing work is untouched, but it '
                     'cannot be selected for new work.',
    STATUS_DECOMMISSIONED: 'Retired for good. Kept for history; cannot be selected for new work.',
}

STATUS_BADGE = {
    STATUS_ACTIVE: 'badge-active',
    STATUS_INACTIVE: 'badge-inactive',
    STATUS_DECOMMISSIONED: 'badge-decommissioned',
}

# How deep a parent chain may go. Guards against pathological nesting and gives
# the cycle walks a hard stop even if data is somehow corrupt.
MAX_HIERARCHY_DEPTH = 20


class LifecycleMixin:
    @property
    def status_label(self):
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def status_class(self):
        return STATUS_BADGE.get(self.status, 'badge-inactive')

    @property
    def is_operational(self):
        """Selectable for new work. Named to avoid colliding with User.is_active."""
        return self.status == STATUS_ACTIVE


class HierarchyMixin:
    """Self-referencing parent/child tree.

    Requires the model to define `parent_id`, a `parent` relationship and a
    `children` backref.
    """

    @property
    def ancestors(self):
        """Nearest parent first, walking up to the root."""
        seen, out, node = {self.id}, [], self.parent
        while node is not None and len(out) < MAX_HIERARCHY_DEPTH:
            if node.id in seen:
                break  # defensive: never loop on corrupt data
            seen.add(node.id)
            out.append(node)
            node = node.parent
        return out

    @property
    def descendants(self):
        """Every node beneath this one, breadth-first."""
        out, queue, seen = [], list(self.children), {self.id}
        while queue:
            node = queue.pop(0)
            if node.id in seen:
                continue
            seen.add(node.id)
            out.append(node)
            queue.extend(node.children)
        return out

    @property
    def depth(self):
        return len(self.ancestors)

    @property
    def path(self):
        """Root-first list of names ending with this one, e.g. House > Basement > Utility."""
        return [node.name for node in reversed(self.ancestors)] + [self.name]

    @property
    def path_label(self):
        return ' › '.join(self.path)

    def would_create_cycle(self, candidate_parent):
        """True if re-parenting under `candidate_parent` would form a loop."""
        if candidate_parent is None:
            return False
        if candidate_parent.id == self.id:
            return True
        return any(node.id == candidate_parent.id for node in self.descendants)


ITEM_MATERIAL = 'material'
ITEM_TOOL = 'tool'
ITEM_KINDS = [ITEM_MATERIAL, ITEM_TOOL]

MAX_QUANTITY = 60
MAX_PART_NUMBER = 80


class ItemFieldsMixin:
    """Columns shared by job plan and work order line items.

    The two are the same shape — an ordered line naming a material or tool —
    but they are separate tables on purpose: a work order's list is a *snapshot*
    taken when it was raised, so editing a job plan later must not rewrite the
    history of work already done against the old one.
    """
    kind = db.Column(db.String(20), nullable=False)
    sequence = db.Column(db.Integer, nullable=False, default=1)
    description = db.Column(db.Text, nullable=False)
    quantity = db.Column(db.String(MAX_QUANTITY))
    # Materials only. The whole point of rolling these onto the asset is being
    # able to find the part number again months later.
    part_number = db.Column(db.String(MAX_PART_NUMBER))
