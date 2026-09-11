"""Text search shared by the list pages.

Extracted rather than copied: the work order list and the job plan list want the
same two behaviours — a plain case-insensitive substring match, and an optional
regular expression — and the regex half carries a timeout guard that would be
easy to get subtly wrong a second time.
"""
import time

from app.extensions import db

# A pattern long enough to be pathological is not one anyone typed by hand.
MAX_PATTERN = 200

# The whole regex pass gets this long, not each call. A per-call timeout would
# still allow rows x fields x timeout in total, which on a long list is worse
# than no limit at all.
REGEX_TIME_BUDGET = 2.0

# `regex` rather than the standard library's `re`, for one reason: it accepts a
# timeout. `re` cannot be interrupted, so a pattern with nested quantifiers
# blocks the worker until gunicorn kills the request — and this app runs a
# single worker, so that is the whole instance, for everyone, for two minutes.
# Measured: `(a+)+$` against 29 characters takes 28s under `re`, 1ms under
# `regex`.
try:
    import regex as _regex
except ImportError:                                  # pragma: no cover
    _regex = None


class SearchTooSlow(Exception):
    """The pattern spent its whole budget without finishing."""


def like_clause(needle, *columns):
    """Case-insensitive substring match across the given columns.

    LIKE wildcards in the needle are escaped, so searching for `50%` looks for
    that text rather than matching everything after `50`. lower() is applied to
    both sides rather than relying on LIKE's own casing, which SQLite only
    applies to ASCII.
    """
    escaped = (needle.replace('\\', '\\\\')
                     .replace('%', '\\%')
                     .replace('_', '\\_')
                     .lower())
    pattern = f'%{escaped}%'
    return db.or_(*[
        db.func.lower(column).like(pattern, escape='\\') for column in columns
    ])


def compile_pattern(needle):
    """Compile a user-supplied pattern. Returns (compiled, error_message).

    Case-insensitive, to match how the plain search behaves — someone toggling
    regex on should not find their search has quietly become case-sensitive too.
    """
    if _regex is None:                               # pragma: no cover
        return None, ('regular expression search is unavailable — the `regex` '
                      'package is not installed')
    if len(needle) > MAX_PATTERN:
        return None, f'patterns are limited to {MAX_PATTERN} characters'
    try:
        return _regex.compile(needle, _regex.IGNORECASE), None
    except _regex.error as error:
        return None, str(error)


def regex_filter(pattern, rows, texts):
    """Rows whose text the pattern matches, within the time budget.

    `texts(row)` yields the strings to search for one row, so a caller can
    include text from related records — a job plan's task descriptions, say.

    Every call is given only the time left, so the pass as a whole cannot run
    longer than REGEX_TIME_BUDGET however many rows it walks.
    """
    deadline = time.monotonic() + REGEX_TIME_BUDGET
    matched = []
    for row in rows:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SearchTooSlow()
        for value in texts(row):
            if not value:
                continue
            try:
                if pattern.search(value, timeout=remaining):
                    matched.append(row)
                    break
            except TimeoutError:
                raise SearchTooSlow()
    return matched


def too_slow_message():
    return (f'that pattern took longer than {REGEX_TIME_BUDGET:g} seconds, so '
            'the search was stopped. Nested quantifiers such as (a+)+ are the '
            'usual cause.')
