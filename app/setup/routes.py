"""First-run setup.

The pattern most self-hosted projects use (Immich, Home Assistant, Nextcloud,
Gitea): while the instance has no users at all, an unauthenticated page offers
to create the first administrator, and it closes permanently the moment one
exists. There is nothing to protect with a password before the first password
exists, so the security of the window comes from how narrow it is:

  * it is open only while the user table is empty — one account closes it
    forever, and nothing can reopen it short of deleting every user;
  * every other page redirects into it, so the instance is obviously
    unconfigured rather than quietly claimable;
  * a loud warning is logged on every start while it is open;
  * SETUP_WINDOW_MINUTES optionally bounds it, the way Portainer does — after
    that the container must be restarted to try again, so an instance left
    running unattended for days is not indefinitely claimable;
  * setting ADMIN_USERNAME/ADMIN_PASSWORD creates the account before anything
    listens, which closes the window entirely. That is the right choice for an
    internet-facing deployment.

The residual risk is real and worth stating plainly: between first start and you
creating the account, whoever reaches the page first becomes the administrator.
Do not expose a fresh instance to an untrusted network before completing setup.
"""
from flask import (
    current_app, flash, redirect, render_template, request, session, url_for,
)
from flask_login import login_user

from app.extensions import db
from app.models.user import User
from app.setup import bp
from app.utils import utcnow, validate_csrf

MIN_PASSWORD_LENGTH = 8


def database_ready():
    """Whether the schema exists yet.

    Wiping instance/ to start over also removes the database, and SQLAlchemy
    will happily create an empty file on connect — so the tables are missing and
    every query fails. Detecting that is the difference between a stack trace
    and a page telling you which command to run.

    The result is cached once true: a database can go from missing to present
    while running (someone applies migrations), but not back.
    """
    if current_app.config.get('_SCHEMA_READY'):
        return True
    try:
        from sqlalchemy import inspect
        ready = inspect(db.engine).has_table('users')
    except Exception:
        return False
    if ready:
        current_app.config['_SCHEMA_READY'] = True
    return ready


def needs_setup():
    """True while the instance has no users at all."""
    return User.query.count() == 0


def window_expired():
    """Whether an optional bounded setup window has passed."""
    minutes = current_app.config.get('SETUP_WINDOW_MINUTES') or 0
    if not minutes:
        return False
    started = current_app.config.get('STARTED_AT')
    if started is None:
        return False
    return (utcnow() - started).total_seconds() > minutes * 60


@bp.route('/setup', methods=['GET', 'POST'])
def first_run():
    if not database_ready():
        return render_template('setup/no_database.html'), 503
    if not needs_setup():
        # Closed for good; nothing here can create a second administrator.
        return redirect(url_for('auth.login'))

    if window_expired():
        return render_template('setup/expired.html',
                               minutes=current_app.config['SETUP_WINDOW_MINUTES']), 403

    if request.method == 'POST':
        validate_csrf()
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        errors = []
        if not username:
            errors.append('Choose a username.')
        if '@' not in email:
            errors.append('Enter a valid email address.')
        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f'The password must be at least {MIN_PASSWORD_LENGTH} characters.')
        elif password != confirm:
            errors.append('The passwords do not match.')

        if errors:
            for message in errors:
                flash(message, 'error')
            return render_template('setup/index.html', username=username, email=email)

        # Re-checked inside the request: two people could have opened the page
        # at the same time, and only the first should win.
        if not needs_setup():
            flash('An administrator already exists. Please sign in.', 'error')
            return redirect(url_for('auth.login'))

        user = User(username=username, email=email, role='admin')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        current_app.logger.info('First administrator %r created; setup is now closed.',
                                username)
        login_user(user)
        session.permanent = True
        flash('Welcome. Your administrator account is ready.', 'success')
        return redirect(url_for('main.dashboard'))

    return render_template('setup/index.html', username='', email='')
