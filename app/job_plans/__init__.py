from flask import Blueprint
bp = Blueprint('job_plans', __name__, url_prefix='/job-plans')
from app.job_plans import routes  # noqa: E402, F401
