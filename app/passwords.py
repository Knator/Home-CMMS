"""The password policy, in one place.

Five things set a password — the first-run setup page, the admin create and
edit forms, a user changing their own, and create_admin.py — and each used to
carry its own length check. One rule they all call is what keeps them from
drifting apart, and means the requirements shown on screen are generated from
the same source that enforces them.

Existing passwords are not affected. This applies when a password is set, so
nobody is locked out of an instance that predates the policy; they meet it the
next time they change one.
"""
import string

MIN_LENGTH = 12

# Anything that is not a letter or a digit. Deliberately broad: insisting on a
# specific punctuation set narrows the search space rather than widening it, and
# rejects perfectly good characters from a non-US keyboard.
SPECIAL_CHARACTERS = set(string.punctuation) | {' '}


def _has_upper(password):
    return any(c.isupper() for c in password)


def _has_digit(password):
    return any(c.isdigit() for c in password)


def _has_special(password):
    return any(not c.isalnum() for c in password)


# (requirement shown to the user, test). Order is the order they are displayed.
RULES = (
    (f'At least {MIN_LENGTH} characters', lambda p: len(p) >= MIN_LENGTH),
    ('One capital letter', _has_upper),
    ('One number', _has_digit),
    ('One symbol, such as ! ? # or -', _has_special),
)

REQUIREMENTS = tuple(text for text, _test in RULES)


def password_problems(password):
    """Every requirement this password fails, in display order.

    All of them, not just the first: telling someone their password is too
    short, then that it needs a capital, then that it needs a digit, is three
    round trips to learn one rule set.
    """
    password = password or ''
    return [text for text, test in RULES if not test(password)]


def describe_requirements(separator='; '):
    """The policy as one line, for a tooltip or a command-line prompt."""
    return separator.join(REQUIREMENTS)
