"""Create a super admin, or make an existing account one.

Interactive by default. For unattended setup (a container's first run, a
provisioning script) pass the details as arguments or environment variables:

    python create_admin.py --username kevin --email k@example.com --password ...
    ADMIN_USERNAME=kevin ADMIN_EMAIL=k@example.com ADMIN_PASSWORD=... python create_admin.py

With --if-missing it does nothing when the account already exists, so it is safe
to run on every container start.

    python create_admin.py --promote kevin

makes an existing account a super admin — the way back in when nobody who can
still sign in is one. The account is created as, or promoted to, super admin
because whoever can run this already holds the whole instance: they have the
database file.
"""
import argparse
import getpass
import os
import sys

# Import-time: the app factory reads this, so the CLI never starts the hourly
# PM scheduler thread just to add a user.
os.environ.setdefault('SCHEDULER_ENABLED', '0')

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models.user import ROLE_SUPER_ADMIN, User  # noqa: E402
from app.passwords import (  # noqa: E402
    describe_requirements, password_problems,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Create a super admin, or make an existing account one.')
    parser.add_argument('--username', default=os.environ.get('ADMIN_USERNAME'))
    parser.add_argument('--email', default=os.environ.get('ADMIN_EMAIL'))
    parser.add_argument('--password', default=os.environ.get('ADMIN_PASSWORD'),
                        help='Prefer the environment variable; an argument is '
                             'visible in the process list.')
    parser.add_argument('--if-missing', action='store_true',
                        help='Exit quietly if the user already exists.')
    parser.add_argument('--promote', metavar='USERNAME',
                        help='Make an existing account a super admin, then exit.')
    return parser.parse_args(argv)


def promote(username):
    user = User.query.filter_by(username=username).first()
    if user is None:
        print(f"  error: no account called '{username}'.", file=sys.stderr)
        return 1
    if user.is_super_admin:
        print(f"'{username}' is already a super admin; nothing to do.")
    else:
        user.role = ROLE_SUPER_ADMIN
        db.session.commit()
        print(f"'{username}' is now a super admin.")
    if not user.is_active:
        # Said rather than changed: deactivating someone was a decision, and
        # this was asked to change their role, not undo it.
        print(f"  note: '{username}' is deactivated and cannot sign in until "
              f"another admin reactivates the account.", file=sys.stderr)
    return 0


def collect_interactively():
    username = input('Admin username: ').strip()
    email = input('Admin email: ').strip()
    print(f'Password requirements: {describe_requirements()}')
    password = getpass.getpass('Admin password: ')
    confirm = getpass.getpass('Confirm password: ')
    if password != confirm:
        print('  error: Passwords do not match.', file=sys.stderr)
        return None
    return username, email, password


def validate(username, email, password):
    errors = []
    if not username:
        errors.append('Username is required.')
    elif User.query.filter_by(username=username).first():
        errors.append(f"Username '{username}' already exists.")
    if not email or '@' not in email:
        errors.append('A valid email address is required.')
    elif User.query.filter_by(email=email).first():
        errors.append(f"Email '{email}' is already in use.")
    # Same policy as every web form, so an unattended ADMIN_PASSWORD cannot
    # create an account the app itself would refuse.
    errors.extend(f'Password: {problem.lower()}.' for problem in password_problems(password))
    return errors


def main(argv=None):
    args = parse_args(argv)
    app = create_app()

    with app.app_context():
        if args.promote:
            return promote(args.promote)

        unattended = bool(args.username or args.email or args.password)

        if args.if_missing and args.username and \
                User.query.filter_by(username=args.username).first():
            print(f"Admin user '{args.username}' already exists; nothing to do.")
            return 0

        if unattended:
            username, email, password = args.username, args.email, args.password
        else:
            if not sys.stdin.isatty():
                print('error: no details supplied and no terminal to prompt on. '
                      'Use --username/--email/--password or the ADMIN_* environment '
                      'variables.', file=sys.stderr)
                return 2
            collected = collect_interactively()
            if collected is None:
                return 1
            username, email, password = collected

        errors = validate(username, email, password)
        if errors:
            for message in errors:
                print(f'  error: {message}', file=sys.stderr)
            return 1

        user = User(username=username, email=email, role=ROLE_SUPER_ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Super admin '{username}' created.")
        return 0


if __name__ == '__main__':
    sys.exit(main())
