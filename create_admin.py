"""Create an admin user interactively. Safe to run more than once."""
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


def main():
    app = create_app()
    with app.app_context():
        username = input('Admin username: ').strip()
        email = input('Admin email: ').strip()
        password = getpass.getpass(f'Admin password (min {MIN_PASSWORD_LENGTH} chars): ')
        confirm = getpass.getpass('Confirm password: ')

        errors = []
        if not username:
            errors.append('Username is required.')
        elif User.query.filter_by(username=username).first():
            errors.append(f"Username '{username}' already exists.")
        if '@' not in email:
            errors.append('A valid email address is required.')
        elif User.query.filter_by(email=email).first():
            errors.append(f"Email '{email}' is already in use.")
        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f'Password must be at least {MIN_PASSWORD_LENGTH} characters.')
        elif password != confirm:
            errors.append('Passwords do not match.')

        if errors:
            for e in errors:
                print(f'  error: {e}', file=sys.stderr)
            return 1

        user = User(username=username, email=email, role='admin')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Admin user '{username}' created.")
        return 0


if __name__ == '__main__':
    sys.exit(main())
