#!/usr/bin/env bash
# Menjalankan LumbungDB untuk pengembangan: web dengan auto-reload dan worker pengecek alert.
#   ./start.sh             aplikasi di http://<ip-server>:8037 (ganti lewat PORT=...)
#   ./start.sh wireframe   prototype di http://<ip-server>:8038
#
# Data dev ada di .dev/ (NAS tiruan dan agents.conf) dan container lumbungdev-db.
# Ctrl+C menghentikan web dan worker; lumbungdev-db tetap hidup supaya start berikutnya cepat.
# Reset total: docker rm -f lumbungdev-db && docker volume rm lumbungdev_db && rm -rf .dev
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
IP="$(hostname -I | awk '{print $1}')"

# Hentikan server wireframe versi lama yang memakai port 8037
pkill -f "lumbung-wireframe 8037 " 2>/dev/null || true

if [ "${1:-}" = "wireframe" ]; then
  PORT="${PORT:-8038}"
  echo "Wireframe: http://${IP}:${PORT}"
  # Tanpa cache, supaya perubahan prototype langsung terlihat saat halaman dimuat ulang.
  exec python3 -c '
import functools, http.server, sys
class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()
handler = functools.partial(NoCache, directory=sys.argv[3])
http.server.ThreadingHTTPServer(("0.0.0.0", int(sys.argv[2])), handler).serve_forever()
' lumbung-wireframe "$PORT" "$ROOT/wireframe"
fi

PORT="${PORT:-8037}"
DEV="$ROOT/.dev"
DB_CONTAINER=lumbungdev-db
DB_PORT="${DB_PORT:-33062}"
ADMIN_USER=admin
ADMIN_PASSWORD=admin-dev-password

# ---------- berkas dev ----------
mkdir -p "$DEV/nas" "$DEV/config"
touch "$DEV/nas/.lumbung-mounted"
if [ ! -f "$DEV/config/agents.conf" ]; then
  cp "$ROOT/config/agents.conf.example" "$DEV/config/agents.conf"
  chmod 600 "$DEV/config/agents.conf"
fi
if [ ! -f "$DEV/secret_key" ]; then
  (umask 077; python3 -c "import secrets; print(secrets.token_urlsafe(48))" > "$DEV/secret_key")
fi

# ---------- database ----------
if ! docker container inspect "$DB_CONTAINER" >/dev/null 2>&1; then
  echo "Membuat $DB_CONTAINER di 127.0.0.1:$DB_PORT"
  docker run -d --name "$DB_CONTAINER" --restart unless-stopped \
    -p "127.0.0.1:$DB_PORT:3306" -v lumbungdev_db:/var/lib/mysql \
    -e MARIADB_ROOT_PASSWORD=root -e MARIADB_DATABASE=lumbung \
    -e MARIADB_USER=lumbung -e MARIADB_PASSWORD=lumbung \
    mariadb:11.4 --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci >/dev/null
else
  docker start "$DB_CONTAINER" >/dev/null
fi
echo -n "Menunggu MariaDB"
for _ in $(seq 1 60); do
  if docker exec "$DB_CONTAINER" mariadb -ulumbung -plumbung lumbung -e "SELECT 1" >/dev/null 2>&1; then
    break
  fi
  echo -n "."
  sleep 2
done
echo
docker exec "$DB_CONTAINER" mariadb -ulumbung -plumbung lumbung -e "SELECT 1" >/dev/null

# ---------- aplikasi ----------
export DEBUG=true COOKIE_SECURE=false WEB_CONCURRENCY=1
export DATABASE_URL="mysql+asyncmy://lumbung:lumbung@127.0.0.1:$DB_PORT/lumbung"
export SECRET_KEY="$(cat "$DEV/secret_key")"
export AGENTS_FILE="$DEV/config/agents.conf" DATA_DIR="$DEV/nas"

cd "$ROOT/server"
uv sync --quiet
[ -s app/static/js/vendor/htmx.min.js ] || ./scripts/fetch_vendor.sh
uv run --quiet alembic upgrade head 2>/dev/null

users="$(docker exec "$DB_CONTAINER" mariadb -ulumbung -plumbung lumbung -BNe "SELECT COUNT(*) FROM users")"
if [ "$users" = "0" ]; then
  printf '%s\n' "$ADMIN_PASSWORD" | uv run --quiet python -m app.auth.cli create-user "$ADMIN_USER" >/dev/null
fi

worker_pid=""
web_pid=""
stop_all() {
  trap - EXIT INT TERM
  [ -z "$web_pid" ] || kill -TERM "$web_pid" 2>/dev/null || true
  [ -z "$worker_pid" ] || kill -TERM "$worker_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap stop_all EXIT
trap 'stop_all; exit 0' INT TERM

uv run --quiet python -m app.alerts.worker > "$DEV/worker.log" 2>&1 &
worker_pid=$!

echo
echo "LumbungDB dev : http://${IP}:${PORT}"
if [ "$users" = "0" ]; then
  echo "Login         : ${ADMIN_USER} / ${ADMIN_PASSWORD}"
else
  echo "Login         : akun yang sudah ada (bawaan ${ADMIN_USER} / ${ADMIN_PASSWORD})"
fi
echo "NAS tiruan    : $DEV/nas"
echo "Log worker    : $DEV/worker.log"
echo

# Di latar belakang lalu wait: trap langsung berjalan saat sinyal datang, tidak menunggu uvicorn selesai
uv run --quiet uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload --reload-dir app --no-access-log &
web_pid=$!
wait "$web_pid"
