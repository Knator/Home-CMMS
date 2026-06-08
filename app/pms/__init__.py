from flask import Blueprint
bp = Blueprint('pms', __name__, url_prefix='/pms')
from app.pms import routes  # noqa: E402, F401
