# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /srv

# Tanpa COPY: image hanya berisi dependency. Kode (app, migrations, alembic.ini) di-bind mount
# dari ./server oleh docker-compose.yml, jadi perubahan kode cukup `docker compose restart`.
# Venv di /opt/venv, bukan /srv/.venv, supaya tidak tertutup bind mount.
# Rebuild hanya bila pyproject.toml atau uv.lock berubah.
RUN --mount=from=ghcr.io/astral-sh/uv:0.12,source=/uv,target=/usr/local/bin/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project

RUN useradd --system --no-create-home app
USER app

EXPOSE 8000

# Satu image untuk web, migrasi (`alembic upgrade head`), pengecek (`python -m app.alerts.worker`),
# dan CLI akun (`python -m app.auth.cli`).
# WEB_CONCURRENCY: mulai dari jumlah core; perhitungkan di rumus koneksi DB.
# --timeout-keep-alive 75 > keepalive_timeout upstream nginx (60 s): yang menutup koneksi idle selalu nginx,
# bukan uvicorn (default uvicorn 5 s → 502 sesekali pada POST).
# --no-access-log: access log teks uvicorn diganti log JSON "request" ber-request_id (app/core/logging.py).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1} --timeout-keep-alive 75 --no-access-log --no-server-header"]
