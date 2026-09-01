import os

from flask import render_template, redirect, url_for, flash, request, send_file, abort
from flask_login import login_required, current_user

from app.admin import bp
from app.extensions import db
from app.models.user import User
from app.models.api_token import ApiToken
from app.utils import validate_csrf, admin_required, parse_int
from app import maintenance


def _other_active_admins(user_id):
    return User.query.filter(
        User.role == 'admin', User.is_active.is_(True), User.id != user_id
    ).count()


@bp.route('/users')
@login_required
@admin_required
def users():
    all_users = User.query.order_by(User.username).all()
    return render_template('admin/users.html', users=all_users)


@bp.route('/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    if request.method == 'POST':
        validate_csrf()
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'user')
        if role not in ('admin', 'user'):
            role = 'user'

        errors = []
        if not username:
            errors.append('Username is required.')
        if '@' not in email:
            errors.append('A valid email address is required.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if username and User.query.filter_by(username=username).first():
            errors.append('Username already taken.')
        if email and User.query.filter_by(email=email).first():
            errors.append('Email already in use.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('admin/user_form.html', user=None)

        user = User(username=username, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('User created.', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_form.html', user=None)


@bp.route('/users/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(id):
    user = db.get_or_404(User, id)
    if request.method == 'POST':
        validate_csrf()
        email = request.form.get('email', '').strip()
        role = request.form.get('role', 'user')
        if role not in ('admin', 'user'):
            role = 'user'
        new_password = request.form.get('new_password', '')

        if '@' not in email:
            flash('A valid email address is required.', 'error')
            return render_template('admin/user_form.html', user=user)

        existing = User.query.filter_by(email=email).first()
        if existing and existing.id != id:
            flash('Email already in use.', 'error')
            return render_template('admin/user_form.html', user=user)

        # Losing the last admin would leave user management unreachable.
        if user.role == 'admin' and role != 'admin' and _other_active_admins(id) == 0:
            flash('This is the only administrator; assign another admin first.', 'error')
            return render_template('admin/user_form.html', user=user)

        if new_password and len(new_password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('admin/user_form.html', user=user)

        user.email = email
        user.role = role
        if new_password:
            user.set_password(new_password)

        db.session.commit()
        flash('User updated.', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_form.html', user=user)


@bp.route('/users/<int:id>/api-token', methods=['POST'])
@login_required
@admin_required
def issue_api_token(id):
    """Create a named token. The plaintext is shown once and never stored."""
    validate_csrf()
    user = db.get_or_404(User, id)

    name = request.form.get('token_name', '').strip()
    if not name:
        flash('Give the token a name so you can tell your integrations apart.', 'error')
        return redirect(url_for('admin.edit_user', id=id))

    token, plaintext = ApiToken.issue(user, name)
    db.session.commit()
    # Handed to the template separately from any prose, so it can be copied on
    # its own rather than selected out of a sentence.
    flash(plaintext, 'token')
    flash(f"Token '{token.name}' created for {user.username}.", 'success')
    return redirect(url_for('admin.edit_user', id=id))


@bp.route('/users/<int:id>/api-token/<int:token_id>/revoke', methods=['POST'])
@login_required
@admin_required
def revoke_api_token(id, token_id):
    validate_csrf()
    user = db.get_or_404(User, id)
    token = db.session.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        flash('That token could not be found.', 'error')
        return redirect(url_for('admin.edit_user', id=id))

    name = token.name
    db.session.delete(token)
    db.session.commit()
    flash(f"Token '{name}' revoked. Anything using it will stop working immediately.",
          'success')
    return redirect(url_for('admin.edit_user', id=id))


@bp.route('/users/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_user(id):
    validate_csrf()
    user = db.get_or_404(User, id)

    if user.id == current_user.id:
        flash('You cannot deactivate your own account.', 'error')
        return redirect(url_for('admin.users'))
    if user.is_active and user.role == 'admin' and _other_active_admins(id) == 0:
        flash('This is the only active administrator; assign another admin first.', 'error')
        return redirect(url_for('admin.users'))

    user.is_active = not user.is_active
    db.session.commit()
    state = 'activated' if user.is_active else 'deactivated'
    flash(f'User {state}.', 'success')
    return redirect(url_for('admin.users'))


# ── Maintenance ────────────────────────────────────────────────────────────

def _wants_async():
    """True when the maintenance page fetched this rather than navigating to it."""
    return request.headers.get('X-Requested-With') == 'fetch'


def _render_maintenance(scan=False):
    return render_template(
        'admin/maintenance.html',
        status=maintenance.system_status(),
        scheduler=maintenance.scheduler_status(),
        backups=maintenance.list_backups(),
        scan=maintenance.scan_storage() if scan else None,
    )


def _maintenance_result(scan=False):
    """Re-render in place for a fetch; redirect as usual for a plain form post.

    The server stays the only thing that renders this page — the browser just
    swaps in the new markup, so there is no duplicate rendering logic in JS.
    """
    if _wants_async():
        return _render_maintenance(scan=scan)
    return redirect(url_for('admin.maintenance_page', **({'scan': 1} if scan else {})))


@bp.route('/maintenance')
@login_required
@admin_required
def maintenance_page():
    return _render_maintenance(scan=bool(request.args.get('scan')))


@bp.route('/maintenance/backup', methods=['POST'])
@login_required
@admin_required
def create_backup():
    validate_csrf()
    try:
        result = maintenance.create_backup()
    except Exception:
        current_app_logger = __import__('flask').current_app.logger
        current_app_logger.exception('Backup failed')
        flash('Backup failed. Check the application log for details.', 'error')
        return _maintenance_result()

    flash(f"Backup {result['name']} created "
          f"({result['size'] // 1024} KB in {result['seconds']}s).", 'success')

    keep = parse_int(request.form.get('keep'), minimum=1)
    if keep:
        pruned = maintenance.prune_backups(keep)
        if pruned:
            flash(f"Removed {pruned} older backup{'' if pruned == 1 else 's'}.", 'info')
    return _maintenance_result()


@bp.route('/maintenance/backup/<path:name>/download')
@login_required
@admin_required
def download_backup(name):
    # Validated rather than joined blindly: this path comes from the URL.
    if not maintenance.is_backup_name(name):
        abort(404)
    path = os.path.join(maintenance.backup_dir(), name)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=name)


@bp.route('/maintenance/backup/<path:name>/delete', methods=['POST'])
@login_required
@admin_required
def delete_backup(name):
    validate_csrf()
    if maintenance.delete_backup(name):
        flash(f'Backup {name} deleted.', 'success')
    else:
        flash('That backup could not be found.', 'error')
    return _maintenance_result()


@bp.route('/maintenance/clean-storage', methods=['POST'])
@login_required
@admin_required
def clean_storage():
    validate_csrf()
    removed = maintenance.clean_storage()
    if removed['rows'] or removed['files'] or removed['thumbnails']:
        flash(
            f"Removed {removed['rows']} attachment record(s), {removed['files']} orphaned "
            f"file(s) and {removed['thumbnails']} stale thumbnail(s), "
            f"freeing {removed['bytes'] // 1024} KB.", 'success')
    else:
        flash('Nothing to clean up.', 'info')
    return _maintenance_result(scan=True)


@bp.route('/maintenance/clear-thumbnails', methods=['POST'])
@login_required
@admin_required
def clear_thumbnails():
    validate_csrf()
    result = maintenance.clear_thumbnail_cache()
    flash(f"Cleared {result['removed']} thumbnail(s), freeing {result['bytes'] // 1024} KB. "
          "They rebuild automatically when next viewed.", 'success')
    return _maintenance_result()


@bp.route('/maintenance/check-database', methods=['POST'])
@login_required
@admin_required
def check_database():
    validate_csrf()
    result = maintenance.check_database()
    if result['ok']:
        flash('Database integrity check passed.', 'success')
    else:
        flash(f"Integrity check reported problems: {'; '.join(result['integrity'])} "
              f"({result['foreign_key_violations']} foreign key violation(s)).", 'error')
    return _maintenance_result()


@bp.route('/maintenance/vacuum', methods=['POST'])
@login_required
@admin_required
def vacuum_database():
    validate_csrf()
    result = maintenance.vacuum_database()
    flash(f"Database rebuilt. Reclaimed {result['reclaimed'] // 1024} KB "
          f"({result['before'] // 1024} KB to {result['after'] // 1024} KB).", 'success')
    return _maintenance_result()


@bp.route('/maintenance/checkpoint', methods=['POST'])
@login_required
@admin_required
def checkpoint_wal():
    validate_csrf()
    result = maintenance.checkpoint_wal()
    flash(f"Write-ahead log folded in ({result['checkpointed']} pages).", 'success')
    return _maintenance_result()


@bp.route('/maintenance/run-pm-check', methods=['POST'])
@login_required
@admin_required
def run_pm_check_now():
    validate_csrf()
    from flask import current_app
    from app.scheduler import run_pm_check

    generated = run_pm_check(current_app._get_current_object())
    if generated:
        flash(f"PM check generated {generated} work order(s).", 'success')
    else:
        flash('PM check ran; nothing was due.', 'info')
    return _maintenance_result()
