# Finance Tracker container image.
#
# All persistent data (database, backups, per-browser storage) lives in /data,
# so mount a volume there. See docker-compose.yml.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# tzdata lets you set TZ (e.g. Europe/London) so "today" and the current year
# on the Overview/Transactions pages match your local time, not UTC.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user. /data is created and owned by it before the
# VOLUME line, so a fresh named volume inherits the right permissions.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown app:app /data

WORKDIR /app

# Install dependencies first so this layer is cached until requirements change.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .

# Keep everything the app writes inside /data.
ENV LANTERN_DB_PATH=/data/lantern.db \
    LANTERN_BACKUP_DIR=/data/backups \
    NICEGUI_STORAGE_PATH=/data/.nicegui \
    LANTERN_PORT=8080

USER app
VOLUME /data
EXPOSE 8080

# Only checks that the server is accepting connections (the pages themselves
# may call the exchange-rate API, so they make a poor health probe).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,socket; socket.create_connection(('127.0.0.1', int(os.environ.get('LANTERN_PORT', '8080'))), 3).close()"

CMD ["python", "main.py"]
