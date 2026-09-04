"""Create an admin user.

Interactive by default. For unattended setup (a container's first run, a
provisioning script) pass the details as arguments or environment variables:

    python create_admin.py --username kevin --email k@example.com --password ...
    ADMIN_USERNAME=kevin ADMIN_EMAIL=k@example.com ADMIN_PASSWORD=... python create_admin.py

With --if-missing it does nothing when the account already exists, so it is safe
to run on every container start.
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
from app.models.user import User  # noqa: E402

MIN_PASSWORD_LENGTH = 8


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Create an admin user.')
    parser.add_argument('--username', default=os.environ.get('ADMIN_USERNAME'))
    parser.add_argument('--email', default=os.environ.get('ADMIN_EMAIL'))
    parser.add_argument('--password', default=os.environ.get('ADMIN_PASSWORD'),
                        help='Prefer the environment variable; an argument is '
                             'visible in the process list.')
    parser.add_argument('--if-missing', action='store_true',
                        help='Exit quietly if the user already exists.')
    return parser.parse_args(argv)


def collect_interactively():
    username = input('Admin username: ').strip()
    email = input('Admin email: ').strip()
    password = getpass.getpass(f'Admin password (min {MIN_PASSWORD_LENGTH} chars): ')
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
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f'Password must be at least {MIN_PASSWORD_LENGTH} characters.')
    return errors


def main(argv=None):
    args = parse_args(argv)
    app = create_app()

    with app.app_context():
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

        user = User(username=username, email=email, role='admin')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Admin user '{username}' created.")
        return 0


if __name__ == '__main__':
    sys.exit(main())
