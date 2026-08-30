from flask import render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user

from app.auth import bp
from app.extensions import db
from app.models.user import User
from app.utils import validate_csrf, safe_redirect, utcnow


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        validate_csrf()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(username=username).first()
        if user and user.is_active and user.check_password(password):
            login_user(user, remember=remember)
            user.last_login = utcnow()
            db.session.commit()
            # Only same-origin relative paths; an absolute URL here would turn
            # the login page into an open redirect.
            return safe_redirect(request.args.get('next'))

        flash('Invalid username or password.', 'error')

    return render_template('auth/login.html')


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('auth.login'))


@bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        validate_csrf()
        current_pw = request.form.get('current_password', '')
        new_pw = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')

        if not current_user.check_password(current_pw):
            flash('Current password is incorrect.', 'error')
        elif len(new_pw) < 8:
            flash('New password must be at least 8 characters.', 'error')
        elif new_pw != confirm_pw:
            flash('Passwords do not match.', 'error')
        else:
            current_user.set_password(new_pw)
            db.session.commit()
            flash('Password updated successfully.', 'success')
            return redirect(url_for('main.dashboard'))

    return render_template('auth/change_password.html')
