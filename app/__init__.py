import os
from flask import Flask
from app.extensions import db, migrate, login_manager
from app.utils import generate_csrf_token, format_file_size
from config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    from app.models.user import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    app.jinja_env.globals['csrf_token'] = generate_csrf_token
    app.jinja_env.globals['format_file_size'] = format_file_size

    from app.auth import bp as auth_bp
    from app.main import bp as main_bp
    from app.locations import bp as locations_bp
    from app.assets import bp as assets_bp
    from app.work_orders import bp as work_orders_bp
    from app.job_plans import bp as job_plans_bp
    from app.pms import bp as pms_bp
    from app.admin import bp as admin_bp
    from app.attachments import bp as attachments_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(locations_bp)
    app.register_blueprint(assets_bp)
    app.register_blueprint(work_orders_bp)
    app.register_blueprint(job_plans_bp)
    app.register_blueprint(pms_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(attachments_bp)

    # Start PM scheduler (skip in reloader subprocess)
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        from app.scheduler import start_scheduler
        start_scheduler(app)

    return app
