FROM python:3.12-slim

# keep python from writing pyc files / buffering stdout in the container
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /app

# the slim base ships no system CA store; without it Python's urllib can't verify
# TLS (pip gets away with it via its own bundled certs). We hit HTTPS at runtime for
# Open Food Facts lookups, so install the certs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# the sqlite db, backups and csv mirror all live under /data (a mounted volume)
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# gunicorn serves the app factory's module-level `app`
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
