import logging
import os

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from app.extensions import db, migrate, login_manager
from app.utils import (
    generate_csrf_token, format_file_size, format_duration, thumbnails_available,
)
from config import Config


def create_app(config_class=Config, config_overrides=None):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config.setdefault(
        'SCHEDULER_ENABLED',
        os.environ.get('SCHEDULER_ENABLED', '1') not in ('0', 'false', 'False'),
    )
    if config_overrides:
        app.config.update(config_overrides)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    )

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    _ensure_sqlite_directory(app)

    db.init_app(app)
    # SQLite cannot ALTER most things in place, so Alembic has to rewrite tables
    # to apply a migration.
    migrate.init_app(app, db, render_as_batch=True)
    login_manager.init_app(app)

    from app.models.user import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            user = db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None
        # Re-check on every request: deactivating a user must end sessions that
        # are already signed in, not just block future logins.
        if user is None or not user.is_active:
            return None
        return user

    app.jinja_env.globals['csrf_token'] = generate_csrf_token
    app.jinja_env.globals['format_file_size'] = format_file_size
    app.jinja_env.globals['format_duration'] = format_duration
    # Checked once at startup: without Pillow the templates skip previews
    # entirely rather than falling back to full-size images.
    app.jinja_env.globals['thumbnails_available'] = thumbnails_available()
    if not app.jinja_env.globals['thumbnails_available']:
        app.logger.warning(
            'Pillow is not installed, so image previews are disabled. '
            'Install it with: pip install -r requirements.txt'
        )

    from app.auth import bp as auth_bp
    from app.main import bp as main_bp
    from app.locations import bp as locations_bp
    from app.assets import bp as assets_bp
    from app.work_orders import bp as work_orders_bp
    from app.job_plans import bp as job_plans_bp
    from app.pms import bp as pms_bp
    from app.admin import bp as admin_bp
    from app.attachments import bp as attachments_bp
    from app.api import bp as api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(locations_bp)
    app.register_blueprint(assets_bp)
    app.register_blueprint(work_orders_bp)
    app.register_blueprint(job_plans_bp)
    app.register_blueprint(pms_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(attachments_bp)
    app.register_blueprint(api_bp)

    def _is_api_request():
        return request.path.startswith('/api/')

    # Flask raises routing errors before a blueprint is known, so these live at
    # app level and check the path. Without them an API client gets an HTML
    # error page where it expects JSON.
    @app.errorhandler(404)
    def api_aware_not_found(error):
        if _is_api_request():
            return jsonify({'error': 'Not found.'}), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(405)
    def api_aware_method_not_allowed(error):
        if _is_api_request():
            return jsonify({'error': 'Method not allowed for this endpoint.'}), 405
        return error, 405

    @app.errorhandler(500)
    def api_aware_server_error(error):
        if _is_api_request():
            return jsonify({'error': 'Internal server error.'}), 500
        return error, 500

    @app.errorhandler(413)
    def file_too_large(error):
        limit_mb = app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)
        flash(f'That file is too large. The limit is {limit_mb} MB.', 'error')
        return redirect(request.referrer or url_for('main.dashboard')), 302

    # Start the PM scheduler. Skipped in the reloader's parent process (it would
    # run twice), and skipped entirely by the CLI scripts and the test suite,
    # which set SCHEDULER_ENABLED=0.
    if app.config['SCHEDULER_ENABLED'] and (
        not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    ):
        from app.scheduler import start_scheduler
        start_scheduler(app)

    return app


def _ensure_sqlite_directory(app):
    uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    prefix = 'sqlite:///'
    if not uri.startswith(prefix):
        return
    path = uri[len(prefix):]
    if path and path != ':memory:':
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
