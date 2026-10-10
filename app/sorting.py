"""Sortable list headings, shared by every list page.

Done by the database, before the page is cut: the browser only holds fifty
rows, so sorting there would only rearrange page one. `sort` and `dir` are two
more query parameters, which is why they survive the trip to a record and back
like the filters do, and why a record's previous/next arrows follow the same
order — each list builds both from one query.

Each list declares its own `{key: SortKey}`; nothing from the request reaches
SQL except by naming one of those keys. Extracted rather than copied, like
app/search.py, because the details are easy to get subtly wrong a second time:
blanks, the tiebreak, and what an unknown key does.
"""
from collections import namedtuple

from sqlalchemy import case

DIRECTIONS = ('asc', 'desc')
SORT_ARGS = ('sort', 'dir')


class SortKey(namedtuple('SortKey', 'expression first join')):
    """One sortable column.

    `expression` is a callable returning the SQL to order by — a callable so a
    correlated count can be built against the models at request time. `first`
    is the direction of a first click on the heading: whichever end is most
    likely wanted (soonest due date, highest priority). A second click reverses
    it. `join`, when the value lives on another table, adds that table to the
    query; an outer join, so a row with nothing there is kept, not dropped.
    """
    __slots__ = ()

    def __new__(cls, expression, first='asc', join=None):
        return super().__new__(cls, expression, first, join)


def rank(column, vocabulary):
    """Order by position in the vocabulary rather than alphabetically —
    alphabetical priority is "critical, high, low, medium"."""
    return case({value: i for i, value in enumerate(vocabulary)},
                value=column, else_=len(vocabulary))


class Sorting:
    """The order a list was asked for, as the template needs it."""

    def __init__(self, key, direction, sorts):
        self.key = key
        self.direction = direction
        self._first = {name: spec.first for name, spec in sorts.items()}

    def __bool__(self):
        return self.key is not None

    def is_active(self, key):
        return key == self.key

    def next_direction(self, key):
        """Where a click on `key`'s heading goes: reversed if it is the current
        order, otherwise that column's own first direction."""
        if key == self.key:
            return 'desc' if self.direction == 'asc' else 'asc'
        return self._first[key]

    def args(self):
        """The query parameters that reproduce this order — for a Clear link,
        which drops the filters but not the order."""
        return {'sort': self.key, 'dir': self.direction} if self else {}

    @staticmethod
    def without(list_args):
        """`list_args` with the order taken out: the way back to a tree."""
        return {k: v for k, v in list_args.items() if k not in SORT_ARGS}


def chosen_sort(args, sorts):
    """The order the request asks for.

    An unknown key is ignored rather than refused, like an unknown filter value:
    a hand-edited URL falls back to the list's normal order instead of erroring.
    An unknown direction uses the column's own first direction.
    """
    key = args.get('sort', '')
    if key not in sorts:
        return Sorting(None, None, sorts)
    direction = args.get('dir', '')
    if direction not in DIRECTIONS:
        direction = sorts[key].first
    return Sorting(key, direction, sorts)


def apply_sort(query, sorting, sorts, default):
    """`query` ordered as `sorting` asks, falling back to `default`.

    `default` is the list's normal order and doubles as the tiebreak for every
    sort, so it must end in something unique (an id). Without that, rows that
    tie — four hundred "open" work orders — have no fixed order between page
    loads, so one can turn up on two pages while another appears on neither.
    """
    if not sorting:
        return query.order_by(*default)

    spec = sorts[sorting.key]
    if spec.join is not None:
        query = spec.join(query)
    expression = spec.expression()
    # Blanks count as the lowest value, so they lead ascending and trail
    # descending: either end of a column is one click away, including "every
    # work order with no asset". Stated rather than left to the database, which
    # is SQLite's default but the opposite of PostgreSQL's.
    if sorting.direction == 'asc':
        ordered = expression.asc().nulls_first()
    else:
        ordered = expression.desc().nulls_last()
    return query.order_by(ordered, *default)
