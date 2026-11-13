# Runner LumbungDB: Python bawaan + klien Docker. Alat restore (age, mbstream, mariadb-backup) ada di image MariaDB
# yang dijalankan runner lewat docker.sock, bukan di image ini.
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends docker.io ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --chmod=644 runner.py /opt/lumbung/runner.py
# Versi runner, mis. r2: dilaporkan lewat heartbeat dan dibandingkan dengan versi terbaru di LumbungDB.
ARG RUNNER_VERSION=dev
RUN echo "$RUNNER_VERSION" >/opt/lumbung/VERSION
ENTRYPOINT ["python3", "-I", "/opt/lumbung/runner.py"]
