from flask import Blueprint
bp = Blueprint('assets', __name__, url_prefix='/assets')
from app.assets import routes  # noqa: E402, F401
