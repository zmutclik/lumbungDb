# Runner LumbungDB: Python bawaan + klien Docker. Alat restore (age, mbstream, mariadb-backup) ada di image MariaDB
# yang dijalankan runner lewat docker.sock, bukan di image ini.
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends docker.io ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --chmod=644 runner.py /opt/lumbung/runner.py
ENTRYPOINT ["python3", "-I", "/opt/lumbung/runner.py"]
