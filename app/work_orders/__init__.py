from flask import Blueprint
bp = Blueprint('work_orders', __name__, url_prefix='/work-orders')
from app.work_orders import routes  # noqa: E402, F401
