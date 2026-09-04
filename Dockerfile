# Home CMMS — self-hosted maintenance management.
#
# One process, one worker: the database is SQLite (a single writer) and the PM
# scheduler runs inside the app, so a second worker would mean duplicate work
# orders and lock contention. That is ample for a household.

FROM python:3.13-slim AS base

# tzdata so TZ=Europe/London and friends resolve; the app reads the system
# timezone and needs no network for it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so application edits do not invalidate this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Run as an unprivileged user. The two data directories are created here and
# owned by that user, so bind mounts work without a chown on the host.
RUN useradd --create-home --uid 10001 cmms \
    && mkdir -p /app/instance /app/uploads \
    && chown -R cmms:cmms /app/instance /app/uploads \
    && chmod +x /app/docker/entrypoint.sh

USER cmms

ENV FLASK_APP=run.py \
    UPLOAD_FOLDER=/app/uploads \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

# Everything that must survive a container being replaced.
VOLUME ["/app/instance", "/app/uploads"]

# The login page needs no session, so it is a genuine end-to-end check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/auth/login" > /dev/null || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["serve"]
