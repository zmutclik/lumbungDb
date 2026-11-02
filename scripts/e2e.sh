#!/usr/bin/env bash
# Uji ujung-ke-ujung: LumbungDB lewat nginx, image MariaDB sungguhan, NAS berupa folder lokal.
# Hanya membuat dan menghapus project bernama lumbunge2e*. Pakai: scripts/e2e.sh [--keep]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export E2E_WORK="${E2E_WORK:-$(mktemp -d)}"   # NAS tiruan; /tmp biasanya tmpfs, jadi tidak memakai disk Docker
export E2E_TAG="${E2E_TAG:-12.3-a1}"
export E2E_DB_PASSWORD=e2epass123
export E2E_ADMIN_PASSWORD=e2e-admin-password
export E2E_URL=http://127.0.0.1:18089
# Alamat yang dilihat container MariaDB: network-nya terpisah dari LumbungDB, seperti server database sungguhan.
export E2E_INTERNAL_URL="http://$(hostname -I | awk '{print $1}'):18089"
PROJECT=lumbunge2e
COMPOSE=(docker compose -p "$PROJECT" --env-file "$E2E_WORK/.env")

cleanup() {
    [ "${1:-}" = "--keep" ] && return 0
    docker rm -f lumbunge2e-tls >/dev/null 2>&1 || true
    for name in lumbunge2e-baru lumbunge2e-lama lumbunge2e-pasang; do
        docker rm -f "$name" >/dev/null 2>&1 || true
        docker volume rm "${name}_data" "${name}_backup" >/dev/null 2>&1 || true
        docker network rm "${name}_default" >/dev/null 2>&1 || true
    done
    (cd "$ROOT" && "${COMPOSE[@]}" down -v >/dev/null 2>&1) || true
    docker rmi lumbunge2e-lama-mariadb:latest >/dev/null 2>&1 || true
}

mkdir -p "$E2E_WORK/nas" "$E2E_WORK/config" "$E2E_WORK/tg"
chmod 777 "$E2E_WORK/tg"
touch "$E2E_WORK/nas/.lumbung-mounted"
cp "$ROOT/config/agents.conf.example" "$E2E_WORK/config/agents.conf"; chmod 600 "$E2E_WORK/config/agents.conf"
cat > "$E2E_WORK/.env" <<ENV
BACKUP_ROOT=$E2E_WORK/nas
LUMBUNG_UID=$(id -u)
LUMBUNG_GID=$(id -g)
DB_PASSWORD=$E2E_DB_PASSWORD
SECRET_KEY=e2e-secret-key-e2e-secret-key-e2e-secret-key
APP_SUBNET=172.31.77.0/24
TRUSTED_PROXY_IPS=172.31.77.0/24
NGINX_CONF=nginx-http.conf
HTTP_PORT=18089
HTTPS_PORT=18443
AGENTS_FILE=/config/agents.conf
COOKIE_SECURE=false
CHECK_INTERVAL_SECONDS=5
MOUNT_PROBE_TIMEOUT_SECONDS=3
NAS_STATUS_STALE_SECONDS=30
TELEGRAM_API_BASE=http://telegram:8080
TELEGRAM_TOKEN=e2e-rahasia-telegram
TELEGRAM_CHAT_ID=-100
BACKUP_REQUEST_TTL_MINUTES=1
ALERT_HEARTBEAT_MINUTES=1
IMAGE_TAG_DEFAULT=$E2E_TAG
ENV
mkdir -p "$ROOT/deploy/certs"
# Compose memakai folder config/ di project; untuk uji ini diganti folder kerja lewat override.
cat > "$E2E_WORK/override.yml" <<YML
services:
  web:
    volumes: !override
      - {type: bind, source: $E2E_WORK/nas, target: /data, bind: {propagation: rslave}}
      - $E2E_WORK/config:/config
  worker:
    volumes: !override
      - {type: bind, source: $E2E_WORK/nas, target: /data, bind: {propagation: rslave}}
      - $E2E_WORK/config:/config
      - $ROOT/scripts/sim/probe_hang.py:/srv/app/nas/probe.py:ro
  telegram:
    image: python:3.13-slim
    command: ["python", "/sim/fake_telegram.py", "/tg/pesan.log"]
    volumes:
      - $ROOT/scripts/sim:/sim:ro
      - $E2E_WORK/tg:/tg
YML
COMPOSE+=(-f "$ROOT/docker-compose.yml" -f "$E2E_WORK/override.yml")
export COMPOSE_FILE="$ROOT/docker-compose.yml:$E2E_WORK/override.yml"
export LUMBUNG_ENV_FILE="$E2E_WORK/.env"

cleanup
if [ "${1:-}" != "--keep" ]; then trap cleanup EXIT; fi

free_mb=$(df -m --output=avail /var/lib/docker 2>/dev/null | tail -1 || echo 0)
if [ "${free_mb:-0}" -lt 3000 ]; then
    echo "Ruang disk Docker tinggal ${free_mb} MB; uji butuh setidaknya 3000 MB." >&2
    exit 3
fi

echo "=== Build image MariaDB baru dan lama"
(cd "$ROOT/mariadb" && PUSH=0 MARIADB_VERSION="${E2E_TAG%%-*}" AGENT_VERSION="${E2E_TAG##*-}" ./build-image.sh >/dev/null 2>&1)
OLD_SRC="$(mktemp -d)"
git -C "$ROOT/mariadb" archive HEAD | tar -x -C "$OLD_SRC"
sed -e "s#build: .#build: {context: $OLD_SRC, args: {MARIADB_VERSION: \"${E2E_TAG%%-*}\"}}#" "$OLD_SRC/docker-compose.yml" > "$E2E_WORK/lama-compose.yml"

echo "=== Jalankan LumbungDB"
cd "$ROOT"
"${COMPOSE[@]}" build --quiet web worker
"${COMPOSE[@]}" up -d db
"${COMPOSE[@]}" run --rm -T web alembic upgrade head 2>&1 | tail -1
printf '%s\n' "$E2E_ADMIN_PASSWORD" | "${COMPOSE[@]}" run --rm -T web python -m app.auth.cli create-user admin
"${COMPOSE[@]}" up -d
for _ in $(seq 1 30); do curl -fsS -o /dev/null "$E2E_URL/login" && break; sleep 2; done

echo "=== Uji"
export E2E_WORK
cd "$ROOT/server"
uv run --quiet python "$ROOT/scripts/e2e.py"
