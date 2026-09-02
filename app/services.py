"""Write paths shared by the web routes and the background scheduler."""
import logging
from datetime import date

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.work_order import WorkOrder

log = logging.getLogger(__name__)

MAX_NUMBER_ATTEMPTS = 5


def _is_number_collision(error, *markers):
    """Whether an IntegrityError is about the generated number, not something else.

    Locations carry a second unique index (name-per-parent), so retrying blindly
    would spin five times and then report a misleading "could not allocate a
    number" for what is really a duplicate-name conflict.
    """
    detail = str(getattr(error, 'orig', error)).lower()
    return any(marker.lower() in detail for marker in markers)


def create_work_order(**fields):
    """Insert and commit one work order, assigning the next WO number.

    WO numbers are allocated read-then-write, so a concurrent insert can claim
    the same number; the unique constraint rejects the loser and we retry with a
    freshly read number. Any other pending change in the session is committed in
    the same transaction, which is what lets a PM advance atomically with the
    work order it generated.
    """
    for attempt in range(MAX_NUMBER_ATTEMPTS):
        wo = WorkOrder(wo_number=WorkOrder.generate_wo_number(), **fields)
        db.session.add(wo)
        try:
            db.session.commit()
            return wo
        except IntegrityError as error:
            db.session.rollback()
            if not _is_number_collision(error, 'wo_number'):
                raise
            log.warning(
                "Work order number collision (attempt %d/%d), retrying",
                attempt + 1, MAX_NUMBER_ATTEMPTS,
            )
    raise RuntimeError('Could not allocate a unique work order number.')


def sync_pm_schedule(work_order):
    """Re-anchor a floating PM after its work order's completion changed.

    Called from the work order create and edit paths. Needs the work order's
    completion date already flushed, because the PM re-reads it from the
    database to find the latest completion across all its work orders.
    """
    if work_order.pm_id is None:
        return False
    pm = work_order.source_pm
    if pm is None:
        return False
    db.session.flush()
    return pm.reschedule_from_completion()


def create_asset(**fields):
    """Insert and commit one asset, assigning the next AST- number.

    Same read-then-write race as work order numbers: the unique constraint
    rejects a duplicate and we retry with a freshly read number.
    """
    from app.models.asset import Asset

    for attempt in range(MAX_NUMBER_ATTEMPTS):
        asset = Asset(asset_number=Asset.generate_asset_number(), **fields)
        db.session.add(asset)
        try:
            db.session.commit()
            return asset
        except IntegrityError as error:
            db.session.rollback()
            if not _is_number_collision(error, 'asset_number'):
                raise
            log.warning(
                "Asset number collision (attempt %d/%d), retrying",
                attempt + 1, MAX_NUMBER_ATTEMPTS,
            )
    raise RuntimeError('Could not allocate a unique asset number.')


def create_location(**fields):
    """Insert and commit one location, assigning the next LOC- number."""
    from app.models.location import Location

    for attempt in range(MAX_NUMBER_ATTEMPTS):
        location = Location(location_number=Location.generate_location_number(), **fields)
        db.session.add(location)
        try:
            db.session.commit()
            return location
        except IntegrityError as error:
            db.session.rollback()
            if not _is_number_collision(error, 'location_number'):
                # A duplicate sibling name, say — the caller needs to see that.
                raise
            log.warning(
                "Location number collision (attempt %d/%d), retrying",
                attempt + 1, MAX_NUMBER_ATTEMPTS,
            )
    raise RuntimeError('Could not allocate a unique location number.')


def sibling_name_taken(location, name, parent_id):
    """True if another location under the same parent already uses this name.

    Mirrors the uq_locations_parent_name index (per-parent, case-insensitive) so
    the form can explain the clash instead of surfacing an IntegrityError.
    """
    from app.models.location import Location

    q = Location.query.filter(
        db.func.lower(Location.name) == name.lower(),
        Location.parent_id.is_(None) if parent_id is None else Location.parent_id == parent_id,
    )
    if location is not None and location.id is not None:
        q = q.filter(Location.id != location.id)
    return db.session.query(q.exists()).scalar()


def generate_work_order_for_pm(pm, created_by=None, description=None, on_date=None):
    """Create the PM's next work order and advance the schedule in one transaction.

    Both halves must commit together. If the work order were committed first and
    the advance failed, the PM would still look due and the next scheduler tick
    would generate a duplicate.
    """
    for attempt in range(MAX_NUMBER_ATTEMPTS):
        due_date = pm.next_due_date
        pm.advance_schedule(on_date=on_date)
        wo = WorkOrder(
            wo_number=WorkOrder.generate_wo_number(),
            title=pm.name,
            wo_type='planned',
            status='open',
            priority=pm.wo_priority or 'medium',
            asset_id=pm.asset_id,
            location_id=pm.location_id,
            job_plan_id=pm.job_plan_id,
            pm_id=pm.id,
            due_date=due_date,
            overdue_grace_days=pm.overdue_grace_days or 0,
            description=description or f"Auto-generated from PM schedule: {pm.name}",
            created_by=created_by,
        )
        db.session.add(wo)
        try:
            db.session.commit()
            return wo
        except IntegrityError as error:
            # Rolls back the schedule advance too, so the retry recomputes it
            # from the PM's restored state.
            db.session.rollback()
            if not _is_number_collision(error, 'wo_number'):
                raise
            log.warning(
                "Work order number collision generating PM %s (attempt %d/%d), retrying",
                pm.id, attempt + 1, MAX_NUMBER_ATTEMPTS,
            )
    raise RuntimeError('Could not allocate a unique work order number.')


# ---------------------------------------------------------------------------
# Lifecycle guards
#
# Maximo will not let you delete an asset or location that work has been
# recorded against — the history would be orphaned. We follow that: deletion is
# only offered while nothing references the record, and retiring is done by
# moving it to Inactive or Decommissioned instead.
# ---------------------------------------------------------------------------

def _plural(count, noun):
    return f"{count} {noun}{'' if count == 1 else 's'}"


def location_delete_blockers(location):
    """Human-readable reasons this location cannot be deleted (empty = deletable)."""
    from app.models.asset import Asset
    from app.models.pm import PM
    from app.models.work_order import WorkOrder

    blockers = []
    children = len(location.children)
    if children:
        blockers.append(f"{_plural(children, 'child location')}")
    assets = Asset.query.filter_by(location_id=location.id).count()
    if assets:
        blockers.append(f"{_plural(assets, 'asset')} in it")
    work_orders = WorkOrder.query.filter_by(location_id=location.id).count()
    if work_orders:
        blockers.append(f"{_plural(work_orders, 'work order')}")
    pms = PM.query.filter_by(location_id=location.id).count()
    if pms:
        blockers.append(f"{_plural(pms, 'PM schedule')}")
    return blockers


def asset_delete_blockers(asset):
    """Human-readable reasons this asset cannot be deleted (empty = deletable)."""
    from app.models.pm import PM
    from app.models.work_order import WorkOrder

    blockers = []
    children = len(asset.children)
    if children:
        blockers.append(f"{_plural(children, 'child asset')}")
    work_orders = WorkOrder.query.filter_by(asset_id=asset.id).count()
    if work_orders:
        blockers.append(f"{_plural(work_orders, 'work order')}")
    pms = PM.query.filter_by(asset_id=asset.id).count()
    if pms:
        blockers.append(f"{_plural(pms, 'PM schedule')}")
    return blockers


def selectable_locations(include_id=None):
    """Active locations for a picker, plus whatever the record already points at.

    Without the include_id escape hatch, editing a work order whose location was
    later decommissioned would silently blank the field on save.
    """
    from app.models.location import Location
    from app.models.mixins import STATUS_ACTIVE

    q = Location.query
    if include_id:
        q = q.filter(db.or_(Location.status == STATUS_ACTIVE, Location.id == include_id))
    else:
        q = q.filter(Location.status == STATUS_ACTIVE)
    return q.order_by(Location.name).all()


def selectable_assets(include_id=None):
    from app.models.asset import Asset
    from app.models.mixins import STATUS_ACTIVE

    q = Asset.query
    if include_id:
        q = q.filter(db.or_(Asset.status == STATUS_ACTIVE, Asset.id == include_id))
    else:
        q = q.filter(Asset.status == STATUS_ACTIVE)
    return q.order_by(Asset.name).all()


# ---------------------------------------------------------------------------
# Attachment roll-up
# ---------------------------------------------------------------------------

def related_attachments(wo):
    """Attachments reachable from a work order's associations, newest first.

    Files are never copied — these are links to the originals, so editing the
    asset's manual updates every work order that points at it.

    Sources, most specific first: the PM, the job plan, the asset and its
    ancestors, then the location chain. The location chain starts at the work
    order's own location, or the asset's location when the work order has none.
    """
    from sqlalchemy import and_, or_
    from app.models.attachment import Attachment

    sources = []  # (entity_type, entity_id, label, context name)

    if wo.source_pm:
        sources.append(('pm', wo.source_pm.id, 'PM', wo.source_pm.name))
    if wo.job_plan:
        sources.append(('job_plan', wo.job_plan.id, 'Job Plan', wo.job_plan.name))
    if wo.asset:
        sources.append(('asset', wo.asset.id, 'Asset', wo.asset.name))
        for ancestor in wo.asset.ancestors:
            sources.append(('asset', ancestor.id, 'Parent asset', ancestor.name))

    location = wo.location or (wo.asset.location if wo.asset else None)
    if location:
        sources.append(('location', location.id, 'Location', location.name))
        for ancestor in location.ancestors:
            sources.append(('location', ancestor.id, 'Parent location', ancestor.name))

    if not sources:
        return []

    rows = Attachment.query.filter(
        or_(*[and_(Attachment.entity_type == t, Attachment.entity_id == i)
              for t, i, _, _ in sources])
    ).order_by(Attachment.uploaded_at.desc()).all()

    # Label each attachment with its most specific source; `sources` is already
    # ordered that way, so the first match wins.
    by_key = {}
    for entity_type, entity_id, label, context in sources:
        by_key.setdefault((entity_type, entity_id), (label, context))

    out = []
    for att in rows:
        label, context = by_key[(att.entity_type, att.entity_id)]
        out.append({'attachment': att, 'source_label': label, 'source_name': context})
    return out


def hierarchy_ordered(nodes):
    """Depth-first ordering for tree display: [(node, depth), ...].

    Nodes whose parent is outside `nodes` (filtered out, or NULL) are treated as
    roots so nothing silently disappears from a filtered list.
    """
    by_id = {n.id: n for n in nodes}
    children = {}
    roots = []
    for node in nodes:
        if node.parent_id and node.parent_id in by_id:
            children.setdefault(node.parent_id, []).append(node)
        else:
            roots.append(node)

    out = []

    def walk(node, depth):
        out.append((node, depth))
        for child in sorted(children.get(node.id, []), key=lambda n: n.name.lower()):
            walk(child, depth + 1)

    for root in sorted(roots, key=lambda n: n.name.lower()):
        walk(root, 0)
    return out


# ---------------------------------------------------------------------------
# Materials and tools
# ---------------------------------------------------------------------------

def copy_job_plan_items(work_order):
    """Seed a work order's materials and tools from its job plan.

    Only when the work order has none of its own: re-copying would silently
    discard edits someone made to the list for this particular job.

    Returns how many lines were copied.
    """
    from app.models.job_plan import JobPlan
    from app.models.work_order_item import WorkOrderItem

    if work_order.job_plan_id is None or work_order.items.count():
        return 0

    plan = db.session.get(JobPlan, work_order.job_plan_id)
    if plan is None:
        return 0

    copied = 0
    for source in plan.items.order_by(None).order_by('kind', 'sequence').all():
        db.session.add(WorkOrderItem(
            work_order_id=work_order.id,
            kind=source.kind,
            sequence=source.sequence,
            description=source.description,
            quantity=source.quantity,
            part_number=source.part_number,
        ))
        copied += 1
    return copied


def _match_existing_material(existing, description, part_number):
    """Find the row that already represents this part, if any.

    Matching has to survive a part number being added later — the same rod
    recorded once without a number and once with one is one part, not two. So:
    a part number matches its twin exactly; failing that it may adopt a row that
    has no number and the same description. An item with no number matches on
    description alone.

    A row with a *different* part number is never merged, because two numbers
    mean two parts however similarly they are described.
    """
    number = (part_number or '').strip().lower()
    text = (description or '').strip().lower()

    if number:
        for row in existing:
            if row.normalised_part_number == number:
                return row
        for row in existing:
            if not row.normalised_part_number and row.normalised_description == text:
                return row
        return None

    for row in existing:
        if row.normalised_description == text:
            return row
    return None


def record_materials_on_asset(work_order):
    """Roll a completed work order's materials onto its asset.

    So that months later the asset itself answers "what part did I use?".
    Matching is by part number where there is one, otherwise by description, so
    the same part used repeatedly updates one row instead of piling up.

    Returns (added, updated).
    """
    from app.models.asset_material import AssetMaterial

    if work_order.asset_id is None:
        return 0, 0

    used_on = work_order.completed_date or date.today()
    existing = AssetMaterial.query.filter_by(asset_id=work_order.asset_id).all()

    added = updated = 0
    for item in work_order.materials:
        match = _match_existing_material(existing, item.description, item.part_number)
        if match is None:
            match = AssetMaterial(
                asset_id=work_order.asset_id,
                description=item.description,
                part_number=item.part_number,
                quantity=item.quantity,
                times_used=1,
                first_used_on=used_on,
                last_used_on=used_on,
                last_work_order_id=work_order.id,
            )
            db.session.add(match)
            existing.append(match)
            added += 1
        else:
            match.times_used = (match.times_used or 0) + 1
            match.quantity = item.quantity or match.quantity
            # A part number learned later fills in a row recorded without one.
            match.part_number = match.part_number or item.part_number
            if match.first_used_on is None or used_on < match.first_used_on:
                match.first_used_on = used_on
            if match.last_used_on is None or used_on >= match.last_used_on:
                match.last_used_on = used_on
                match.last_work_order_id = work_order.id
            updated += 1

    return added, updated
