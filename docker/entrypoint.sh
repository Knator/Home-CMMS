#!/bin/sh
# Container entrypoint: bring the schema up to date, optionally seed the first
# admin, then serve.
set -eu

: "${PORT:=8000}"
: "${HOST:=0.0.0.0}"
: "${GUNICORN_TIMEOUT:=120}"

echo "Home CMMS starting"
echo "  timezone : ${TZ:-system default (UTC unless TZ is set)}"
echo "  database : ${DATABASE_URL:-sqlite:///instance/home_cmms.db}"
echo "  uploads  : ${UPLOAD_FOLDER:-/app/uploads}"

# Migrations run before anything serves. Safe to repeat: Alembic applies only
# what is missing.
echo "Applying database migrations..."
flask db upgrade

# Optional unattended bootstrap. Without these the first admin is created with
#   docker compose exec cmms python create_admin.py
if [ -n "${ADMIN_USERNAME:-}" ] && [ -n "${ADMIN_PASSWORD:-}" ]; then
    echo "Ensuring admin user '${ADMIN_USERNAME}' exists..."
    python create_admin.py --if-missing \
        --username "${ADMIN_USERNAME}" \
        --email "${ADMIN_EMAIL:-${ADMIN_USERNAME}@localhost}" \
        --password "${ADMIN_PASSWORD}"
fi

case "${1:-serve}" in
    serve)
        # One worker, deliberately — see the note in the Dockerfile.
        # --timeout is generous because a 100 MB upload on a slow link must not
        # be killed mid-request.
        exec gunicorn \
            --workers 1 \
            --bind "${HOST}:${PORT}" \
            --timeout "${GUNICORN_TIMEOUT}" \
            --access-logfile - \
            --error-logfile - \
            "run:app"
        ;;
    *)
        # Anything else runs verbatim: `docker compose run cmms flask db upgrade`
        exec "$@"
        ;;
esac
