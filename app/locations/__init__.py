from flask import Blueprint
bp = Blueprint('locations', __name__, url_prefix='/locations')
from app.locations import routes  # noqa: E402, F401
