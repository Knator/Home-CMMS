"""Stepping from one record to the next through the list it was opened from.

Every list page carries its filters in the query string, and every row link
passes them on, so a detail page can rebuild the exact sequence the person was
looking at. Nothing is stored server-side: the position lives in the URL, which
means it survives a reload, a bookmark and a shared link, and two people opening
the same link see the same sequence.

The filtering itself differs per entity — different columns, and a hierarchy for
assets and locations — so each module keeps its own. What is identical, and
worth having once, is working out what sits either side of a record.
"""


def neighbours(records, current_id):
    """`(previous, next, position, total)` for `current_id` within `records`.

    All four are None when the record is not in the list. That happens
    routinely — opening an inactive PM from a list showing only active ones, or
    an archived work order from a filter that hides archived — and offering
    arrows into a sequence the record does not belong to would land somewhere
    unrelated. Better to show none.

    `records` is the sequence as displayed, so for the hierarchical lists it is
    the depth-first order after `hierarchy_ordered()`, not the raw query. The
    arrows then walk the page exactly as it reads.
    """
    ids = [record.id for record in records]
    try:
        at = ids.index(current_id)
    except ValueError:
        return None, None, None, None

    previous = records[at - 1] if at > 0 else None
    following = records[at + 1] if at + 1 < len(records) else None
    return previous, following, at + 1, len(records)
