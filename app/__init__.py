import logging
import os

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from app.extensions import db, migrate, login_manager
from app.utils import (
    generate_csrf_token, format_file_size, format_duration, thumbnails_available,
    format_datetime, local_timezone_name, utcnow,
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

    if app.config.get('TRUST_PROXY_HEADERS'):
        # One hop: the reverse proxy directly in front of us. Trusting more
        # would let a client forge the chain.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

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

    # Form templates extend `layout`, so the same template serves the full page
    # and the stripped-down version shown inside a picker modal. Done here
    # rather than in every render_template call, of which there are ten.
    @app.context_processor
    def inject_layout():
        from app.utils import is_embedded
        return {'layout': 'embedded.html' if is_embedded() else 'base.html'}

    from app.settings import archived_deletion_allowed
    app.jinja_env.globals['archived_deletion_allowed'] = archived_deletion_allowed
    app.jinja_env.globals['csrf_token'] = generate_csrf_token
    app.jinja_env.globals['format_file_size'] = format_file_size
    app.jinja_env.globals['format_duration'] = format_duration
    # Timestamps are stored UTC and shown in the host's timezone.
    app.jinja_env.globals['format_datetime'] = format_datetime
    app.jinja_env.globals['local_timezone'] = local_timezone_name
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
    from app.setup import bp as setup_bp

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
    app.register_blueprint(setup_bp)

    def _is_api_request():
        return request.path.startswith('/api/')

    # Flask raises routing errors before a blueprint is known, so these live at
    # app level and check the path. Without them an API client gets an HTML
    # error page where it expects JSON.
    
    # ── Security response headers ──────────────────────────────────────
    #
    # Set here rather than at a proxy because this is self-hosted software:
    # most instances have no proxy to configure, and one that does may still be
    # reached directly on the LAN, which bypasses the proxy entirely. Headers
    # that travel with the app protect every route on every path by default.
    #
    # HSTS is deliberately absent. It is an assertion about transport, and the
    # app does not terminate TLS — whatever does (Cloudflare, Caddy, nginx) is
    # the only thing that knows whether HTTPS is actually enforced. Sent from
    # here it would also go out over plain http on a LAN address, where the
    # browser ignores it.
    CONTENT_SECURITY_POLICY = '; '.join([
        "default-src 'self'",
        # 'unsafe-inline' is an honest compromise, not an oversight: the
        # templates carry ~35 inline handlers (onclick/onchange) and one inline
        # script. Removing them is a worthwhile refactor, but the directives
        # below do not depend on it and block real attacks today.
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        # The create-record modal frames this app in itself.
        "frame-src 'self'",
        # Clickjacking. Must be 'self', never 'none': that same modal would
        # break. Destructive admin actions are one-click POSTs, and a confirm()
        # dialog is no defence against a framed, invisible page.
        "frame-ancestors 'self'",
        # An injected form cannot post elsewhere — which matters because every
        # form here carries a valid CSRF token.
        "form-action 'self'",
        # Blocks <base href="//evil"> silently repointing every relative URL.
        "base-uri 'self'",
        "object-src 'none'",
    ])

    @app.after_request
    def security_headers(response):
        # setdefault throughout: routes that have already made a deliberate
        # choice keep it. The attachment routes set their own nosniff, and the
        # thumbnail route sets its own long-lived Cache-Control.
        response.headers.setdefault('Content-Security-Policy',
                                    CONTENT_SECURITY_POLICY)
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        # Legacy backstop for frame-ancestors, which older browsers ignore.
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        # Referrers leak record ids and filenames in paths; same-origin keeps
        # them internal without breaking navigation within the app.
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        # Nothing here uses these, so refuse them rather than leave them open.
        response.headers.setdefault(
            'Permissions-Policy',
            'camera=(), microphone=(), geolocation=(), interest-cohort=()')
        return response

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

    @app.before_request
    def apply_upload_limit():
        """Let the stored preference govern the size of an upload.

        MAX_CONTENT_LENGTH is fixed at start-up, but this is a preference an
        administrator can change while the app runs, so it is applied per
        request instead. Set before anything parses a body — the restore routes
        raise it again for themselves afterwards, which still wins because a
        view runs after this.
        """
        from app.settings import begin_request, upload_limit_bytes
        begin_request()
        try:
            request.max_content_length = upload_limit_bytes()
        except Exception:
            pass    # before the settings table exists, the configured cap stands

    @app.before_request
    def require_first_run_setup():
        """Send an unconfigured instance to setup rather than a login it cannot pass."""
        from app.setup.routes import database_ready, needs_setup

        allowed = {'setup.first_run', 'setup.restore', 'static',
                   'api.documentation', 'api.openapi'}
        if request.endpoint in allowed or request.endpoint is None:
            return None

        if not database_ready():
            # An empty or wiped instance directory. Say which command fixes it
            # rather than failing with "no such table".
            if _is_api_request():
                return jsonify({'error': 'The database has not been initialised. '
                                         'Run: flask db upgrade'}), 503
            return render_template('setup/no_database.html'), 503

        if not needs_setup():
            return None
        if _is_api_request():
            return jsonify({'error': 'This instance has not been set up yet.'}), 503
        return redirect(url_for('setup.first_run'))

    @app.errorhandler(413)
    def file_too_large(error):
        from app.settings import upload_limit_bytes
        limit_mb = upload_limit_bytes() // (1024 * 1024)
        flash(f'That file is too large. The limit is {limit_mb} MB — raise '
              'MAX_UPLOAD_MB to accept bigger ones. A backup archive can also be '
              'restored by copying it into instance/backups instead of uploading.',
              'error')
        return redirect(request.referrer or url_for('main.dashboard')), 302

    # Recorded so an optional bounded setup window can be measured from start.
    app.config['STARTED_AT'] = utcnow()

    with app.app_context():
        try:
            from app.setup.routes import needs_setup
            if needs_setup():
                app.logger.warning(
                    'No users exist: first-run setup is OPEN at /setup. Anyone who can '
                    'reach this instance can claim the administrator account until you '
                    'complete it.'
                )
        except Exception:      # a database that is not migrated yet, typically
            app.logger.info('Could not check for existing users yet (run: flask db upgrade).')

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
