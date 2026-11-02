#!/bin/sh
# LumbungDB: memasang satu container MariaDB di server ini. Script ini tidak berisi rahasia.
#   curl -fsSL <url>/install.sh | LUMBUNG_URL=<url> INSTALL_TOKEN=lmi_xxx sh
#
# Pilihan lewat variabel:
#   DATA_PORT=3307     port lain, bila port pilihan di dashboard sudah terpakai (hanya pemasangan pertama)
#   INSTALL_DIR=/path  folder .env dan compose; bawaan <folder tempat perintah dijalankan>/<project>
#
# Aman dijalankan ulang di mesin dan folder yang sama: .env yang sudah ada tetap dipakai, jadi password tidak berubah.
set -eu
umask 077

die() { printf 'GAGAL: %s\n' "$*" >&2; exit 1; }
step() { printf '==> %s\n' "$*"; }
random_text() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32; }
env_value() { sed -n "s/^$1=//p" "$2" | head -n 1; }

port_busy() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | awk '{print $4}' | grep -q "[:.]$1\$"
    else
        return 1   # tidak bisa diperiksa; docker compose yang akan menolak
    fi
}

check_server() {
    step "Memeriksa server"
    command -v curl >/dev/null 2>&1 || die "curl tidak ada."
    command -v docker >/dev/null 2>&1 || die "Docker belum terpasang."
    docker info >/dev/null 2>&1 || die "Tidak bisa memakai Docker. Jalankan sebagai root atau user di grup docker."
    docker compose version >/dev/null 2>&1 || die "Docker Compose v2 belum terpasang."
    [ -s /etc/machine-id ] || die "/etc/machine-id tidak ada; agen memakainya untuk mengenali server ini."
    [ -s /etc/hostname ] || die "/etc/hostname tidak ada; agen memakainya untuk membaca nama server."
}

# Token lewat stdin curl, bukan argumen, supaya tidak terlihat di daftar proses.
request_session() {
    step "Mengambil konfigurasi dari $BASE_URL"
    machine_id=$(head -n 1 /etc/machine-id)
    status=$(curl -sS --max-time 30 -o "$WORK/session" -w '%{http_code}' -K - <<EOF
url = "$BASE_URL/api/install/session"
request = "POST"
header = "X-Install-Token: $TOKEN"
header = "X-Machine-Id: $machine_id"
EOF
    ) || die "Tidak bisa menghubungi $BASE_URL."
    [ "$status" = "200" ] || die "$(head -c 500 "$WORK/session") (HTTP $status)"

    PROJECT=$(env_value COMPOSE_PROJECT_NAME "$WORK/session")
    NAME=$(env_value CONTAINER_NAME "$WORK/session")
    case "$PROJECT" in ''|*[!a-z0-9_-]*) die "Jawaban LumbungDB tidak dikenali." ;; esac
    case "$NAME" in ''|*[!A-Za-z0-9_.-]*) die "Jawaban LumbungDB tidak dikenali." ;; esac
}

choose_dir() {
    DIR=${INSTALL_DIR:-$(pwd)/$PROJECT}
    mkdir -p "$DIR" || die "Tidak bisa membuat $DIR."
}

# Password baru tidak cocok dengan data lama, jadi volume yang sudah ada tidak boleh dipakai diam-diam.
check_fresh() {
    if docker volume inspect "${PROJECT}_data" >/dev/null 2>&1; then
        die "Volume ${PROJECT}_data sudah ada di server ini, tetapi .env tidak ada di $DIR. Bila ini pengulangan, jalankan dari folder yang sama dengan sebelumnya. Untuk container lama, pakai Container lama atau pasang manual di dashboard."
    fi
    if docker container inspect "$NAME" >/dev/null 2>&1; then
        die "Container bernama $NAME sudah ada di server ini."
    fi
}

write_env() {
    if [ -f "$DIR/.env" ]; then
        step "Memakai .env yang sudah ada di $DIR; password tidak berubah"
        token_line=$(grep '^LUMBUNG_TOKEN=' "$WORK/session")
        url_line=$(grep '^LUMBUNG_URL=' "$WORK/session")
        sed -e "s|^LUMBUNG_TOKEN=.*|$token_line|" -e "s|^LUMBUNG_URL=.*|$url_line|" "$DIR/.env" > "$DIR/.env.tmp"
        mv "$DIR/.env.tmp" "$DIR/.env"
        return
    fi
    check_fresh
    port=${PORT_OVERRIDE:-$(env_value DATA_PORT "$WORK/session")}
    case "$port" in ''|*[!0-9]*) die "Port $port tidak valid." ;; esac
    if port_busy "$port"; then
        die "Port $port sudah dipakai. Jalankan ulang dengan port lain: ... | DATA_PORT=3307 LUMBUNG_URL=... INSTALL_TOKEN=... sh"
    fi
    step "Membuat .env di $DIR dengan password acak"
    sed -e "s/^DATA_PORT=.*/DATA_PORT=$port/" \
        -e "s/^DB_ROOTPASSWORD=\$/DB_ROOTPASSWORD=$(random_text)/" \
        -e "s/^DB_APPPASS=\$/DB_APPPASS=$(random_text)/" \
        -e "s/^BACKUP_PASS=\$/BACKUP_PASS=$(random_text)/" \
        "$WORK/session" > "$DIR/.env.tmp"
    chmod 600 "$DIR/.env.tmp"
    mv "$DIR/.env.tmp" "$DIR/.env"
}

start_container() {
    step "Mengambil compose dan menjalankan MariaDB"
    curl -fsS --max-time 30 -o "$WORK/compose.yml" "$BASE_URL/install/docker-compose.lumbung.yml" \
        || die "Tidak bisa mengunduh compose dari $BASE_URL."
    mv "$WORK/compose.yml" "$DIR/docker-compose.lumbung.yml"
    docker compose -p "$PROJECT" -f "$DIR/docker-compose.lumbung.yml" --env-file "$DIR/.env" up -d \
        || die "docker compose gagal. Perbaiki penyebabnya, lalu jalankan perintah yang sama lagi."
}

wait_healthy() {
    step "Menunggu MariaDB sehat"
    waited=0
    while [ "$waited" -lt 300 ]; do
        health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$NAME" 2>/dev/null || true)
        [ "$health" = "healthy" ] && return 0
        [ "$health" = "unhealthy" ] && break
        sleep 5
        waited=$((waited + 5))
    done
    docker logs --tail 20 "$NAME" >&2 2>&1 || true
    die "$NAME belum sehat setelah $waited detik. Log lengkap: docker logs $NAME"
}

report() {
    cat <<EOF

$NAME terpasang.
  Folder      : $DIR
  Port        : $(env_value DATA_PORT "$DIR/.env")
  Database    : $(env_value DB_NAME "$DIR/.env")
  User aplikasi: $(env_value DB_APPUSER "$DIR/.env")
  Password    : ada di $DIR/.env, tidak dikirim ke LumbungDB

Batas bawaan 2 CPU dan 2G memori; ubah DB_CPUS dan DB_MEM_LIMIT di .env, lalu jalankan:
  docker compose -p $PROJECT -f $DIR/docker-compose.lumbung.yml --env-file $DIR/.env up -d
Port terbuka di semua alamat server ini; batasi di firewall hanya untuk IP aplikasi.

Agen mengirim heartbeat dalam 60 detik. Pantau di $BASE_URL/containers/$NAME/verify
EOF
}

main() {
    BASE_URL=${LUMBUNG_URL:-}
    BASE_URL=${BASE_URL%/}
    TOKEN=${INSTALL_TOKEN:-}
    PORT_OVERRIDE=${DATA_PORT:-}
    # Variabel shell mengalahkan --env-file di compose, jadi jangan sampai terbawa.
    unset LUMBUNG_URL INSTALL_TOKEN DATA_PORT 2>/dev/null || true
    case "$BASE_URL" in http://*|https://*) ;; *) die "Isi LUMBUNG_URL, mis. https://lumbung.contoh.id" ;; esac
    case "$TOKEN" in lmi_*) ;; *) die "Isi INSTALL_TOKEN dari halaman Tambah container." ;; esac
    case "$TOKEN$BASE_URL" in *[!A-Za-z0-9_:/.-]*) die "LUMBUNG_URL atau INSTALL_TOKEN berisi karakter yang tidak dikenal." ;; esac

    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    trap 'exit 130' INT TERM

    check_server
    request_session
    choose_dir
    write_env
    start_container
    wait_healthy
    report
}

# Dipanggil di baris terakhir: unduhan yang terpotong tidak menjalankan apa pun. Perintah di dalamnya tidak boleh
# membaca stdin, karena stdin sh adalah isi script ini.
main </dev/null
