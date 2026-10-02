"""Moving through a long list: one page at a time, and record to record.

Every list page carries its filters in the query string, and every row link
passes them on, so a detail page can rebuild the exact sequence the person was
looking at. Nothing is stored server-side: the position lives in the URL, which
means it survives a reload, a bookmark and a shared link, and two people opening
the same link see the same sequence.

**Why pages.** Measured on a seeded database, rendering every work order took
2.5s and 8MB of HTML at 10,000 records, and 11.6s and 41MB at 50,000 — and the
cost was the rendering, not the query. Indexes do not help that; a page size
does, and it is the only fix whose benefit does not decay as the table grows.

The filtering itself differs per entity — different columns, a hierarchy for
assets and locations, and a regular expression that has to run in Python because
SQLite ships no REGEXP. What is identical, and worth having once, is paging and
working out what sits either side of a record.
"""
from math import ceil

# Matches the sign-in log, which was paginated first. Fifty rows is a screen or
# two of scrolling and a page measured in tens of kilobytes rather than tens of
# megabytes.
PAGE_SIZE = 50


class Page:
    """A page of rows filtered in Python rather than SQL.

    Mirrors the part of Flask-SQLAlchemy's Pagination the templates use, so the
    regular-expression path — which cannot be a LIMIT, since the matching
    happens after the rows are loaded — renders through exactly the same markup
    as every other list.
    """

    def __init__(self, items, page, per_page, total, row_total=None):
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = max(1, ceil(total / per_page)) if total else 1
        self.items = items
        # What a person is counting. For a flat list the two are the same; for
        # a tree, `total` is the number of roots, because that is what a page
        # holds — but "5 top-level locations" above a list of 298 reads as a
        # bug, so the visible count stays the record count.
        self.row_total = total if row_total is None else row_total

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages

    @property
    def prev_num(self):
        return self.page - 1 if self.has_prev else None

    @property
    def next_num(self):
        return self.page + 1 if self.has_next else None


def page_number(args):
    """The requested page, clamped to something sane.

    A hand-edited query string is untrusted like any other: a negative or
    non-numeric page becomes the first one rather than an error.
    """
    try:
        return max(1, int(args.get('page', 1)))
    except (TypeError, ValueError):
        return 1


def paginate_list(rows, page, per_page=PAGE_SIZE):
    """One page of an already-materialised list."""
    start = (page - 1) * per_page
    return Page(rows[start:start + per_page], page, per_page, len(rows))


def empty_page(page=1, per_page=PAGE_SIZE):
    """What a list shows when the filter itself was rejected."""
    return Page([], page, per_page, 0)


def neighbours(ids, current_id):
    """`(previous_id, next_id, position, total)` for `current_id` within `ids`.

    Ids rather than records: fetching the id column alone took 99ms where
    loading the objects took 1122ms over 50,000 rows, and the two either side
    are then a primary-key lookup each. The arrows cross page boundaries on
    purpose — stepping should follow the sequence being read, not stop at the
    bottom of a page.

    All four are None when the record is not in the list. That happens
    routinely — an inactive PM opened from a list showing only active ones, an
    archived work order from a filter that hides archived — and arrows into a
    sequence the record does not belong to would land somewhere unrelated.
    """
    try:
        at = ids.index(current_id)
    except ValueError:
        return None, None, None, None

    previous = ids[at - 1] if at > 0 else None
    following = ids[at + 1] if at + 1 < len(ids) else None
    return previous, following, at + 1, len(ids)


def paginate_tree(rows, page, per_page=PAGE_SIZE):
    """One page of an indented tree, split between whole trees.

    `rows` is `hierarchy_ordered()` output — `(node, depth)` depth-first, each
    child directly under its parent. Slicing that flat would strand a child on
    a page with no parent above it: the row would still be indented, under
    nothing, which reads as a rendering fault rather than a page boundary.

    So the unit is the root and everything beneath it. A page therefore holds
    `per_page` top-level records rather than `per_page` rows, and a deeply
    nested one makes its page longer. That is the right trade for a hierarchy
    small enough to be worth drawing as a tree at all.
    """
    groups, current = [], None
    for node, depth in rows:
        if depth == 0 or current is None:
            current = [(node, depth)]
            groups.append(current)
        else:
            current.append((node, depth))

    start = (page - 1) * per_page
    chosen = groups[start:start + per_page]
    return Page([row for group in chosen for row in group],
                page, per_page, len(groups), row_total=len(rows))
