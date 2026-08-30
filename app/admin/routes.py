from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.admin import bp
from app.extensions import db
from app.models.user import User
from app.utils import validate_csrf, admin_required


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
