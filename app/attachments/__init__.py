from flask import Blueprint
bp = Blueprint('attachments', __name__, url_prefix='/attachments')
from app.attachments import routes  # noqa: E402, F401
