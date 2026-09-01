from flask import Blueprint

bp = Blueprint('api', __name__, url_prefix='/api/v1')

from app.api import routes  # noqa: E402, F401
