#!/bin/sh
# LumbungDB: memasang runner uji restore di mesin ini. Script ini tidak berisi rahasia.
#   curl -fsSL <url>/install-runner.sh | LUMBUNG_URL=<url> INSTALL_TOKEN=lmn_xxx sh
#
# Kunci privat age runner harus sudah ada di mesin ini (bawaan /etc/lumbung/age-runner.key, izin 600), dan public
# key-nya tercantum di BACKUP_AGE_RECIPIENTS LumbungDB. Script tidak membuat kunci baru: backup yang sudah ada
# hanya bisa dibuka kunci yang lama.
#
# Pilihan lewat variabel (bila diberikan, menimpa nilai di .env):
#   AGE_IDENTITY_FILE=/path  lokasi kunci privat age runner
#   SCRATCH_DIR=/path        disk kerja untuk restore, sebesar data terbesar yang diuji dikali beberapa
#   INSTALL_DIR=/path        folder .env dan docker-compose.yml; bawaan <folder tempat perintah dijalankan>/lumbung-runner
#
# User biasa yang tidak ada di grup docker cukup punya sudo: perintah docker otomatis lewat sudo (password ditanya sekali).
#
# Aman dijalankan ulang di mesin dan folder yang sama: .env yang sudah ada tetap dipakai; URL, token, dan
# variabel di atas yang diberikan diperbarui.
set -eu
umask 077

PROJECT=lumbung-runner

die() { printf 'GAGAL: %s\n' "$*" >&2; exit 1; }
step() { printf '==> %s\n' "$*"; }
warn() { printf 'PERINGATAN: %s\n' "$*" >&2; }
env_value() { sed -n "s/^$1=//p" "$2" | head -n 1; }
check_path() {
    case "$2" in /*) ;; *) die "$1 harus path absolut: $2" ;; esac
    case "$2" in *[!A-Za-z0-9_./-]*) die "$1 berisi karakter yang tidak dikenal: $2" ;; esac
}

# Semua perintah docker lewat sini; SUDO diisi check_server bila user perlu sudo untuk Docker.
SUDO=
docker() { if [ -n "$SUDO" ]; then sudo docker "$@"; else command docker "$@"; fi; }
as_root() { if [ -n "$SUDO" ]; then sudo "$@"; else "$@"; fi; }

check_server() {
    step "Memeriksa mesin runner"
    command -v curl >/dev/null 2>&1 || die "curl tidak ada."
    command -v docker >/dev/null 2>&1 || die "Docker belum terpasang."
    if ! docker info >/dev/null 2>&1; then
        [ "$(id -u)" != 0 ] && command -v sudo >/dev/null 2>&1 \
            || die "Tidak bisa memakai Docker. Pastikan Docker berjalan (systemctl status docker)."
        step "User $(id -un) tidak bisa memakai Docker langsung; memakai sudo"
        # Password dibaca dari terminal (/dev/tty), bukan stdin, jadi tetap bisa lewat curl | sh.
        sudo -v || die "sudo gagal. Jalankan sebagai root, atau masukkan user ke grup docker."
        SUDO=sudo
        docker info >/dev/null 2>&1 || die "Docker tidak bisa dipakai walau lewat sudo. Pastikan Docker berjalan (systemctl status docker)."
    fi
    docker compose version >/dev/null 2>&1 || die "Docker Compose v2 belum terpasang."
    [ -s /etc/machine-id ] || die "/etc/machine-id tidak ada; runner memakainya untuk mengenali mesin ini."
    [ -s /etc/hostname ] || die "/etc/hostname tidak ada; runner memakainya untuk membaca nama mesin."
}

# Token lewat stdin curl, bukan argumen, supaya tidak terlihat di daftar proses.
request_session() {
    step "Mengambil konfigurasi dari $BASE_URL"
    machine_id=$(head -n 1 /etc/machine-id)
    status=$(curl -sS --max-time 30 -o "$WORK/session" -w '%{http_code}' -K - <<EOF
url = "$BASE_URL/api/install/runner-session"
request = "POST"
header = "X-Install-Token: $TOKEN"
header = "X-Machine-Id: $machine_id"
EOF
    ) || die "Tidak bisa menghubungi $BASE_URL."
    [ "$status" = "200" ] || die "$(head -c 500 "$WORK/session") (HTTP $status)"

    NAME=$(env_value RUNNER_NAME "$WORK/session")
    RECIPIENTS=$(env_value LUMBUNG_AGE_RECIPIENTS "$WORK/session")
    case "$NAME" in ''|*[!A-Za-z0-9_.-]*) die "Jawaban LumbungDB tidak dikenali." ;; esac
    grep -q '^LUMBUNG_RUNNER_TOKEN=.' "$WORK/session" || die "Jawaban LumbungDB tidak dikenali."
}

choose_dir() {
    DIR=${INSTALL_DIR:-$(pwd)/$PROJECT}
    mkdir -p "$DIR" || die "Tidak bisa membuat $DIR."
}

write_env() {
    token_line=$(grep '^LUMBUNG_RUNNER_TOKEN=' "$WORK/session")
    url_line=$(grep '^LUMBUNG_URL=' "$WORK/session")
    # Pemasangan lama menulis .env.runner; dipindah ke .env.
    if [ -f "$DIR/.env.runner" ] && ! [ -f "$DIR/.env" ]; then
        mv "$DIR/.env.runner" "$DIR/.env"
    fi
    if [ -f "$DIR/.env" ]; then
        step "Memakai .env yang sudah ada di $DIR; URL dan token diperbarui"
        sed -e "s|^LUMBUNG_RUNNER_TOKEN=.*|$token_line|" -e "s|^LUMBUNG_URL=.*|$url_line|" \
            "$DIR/.env" > "$DIR/.env.tmp"
    else
        step "Membuat .env di $DIR"
        {
            printf '# Runner %s. Dibuat install-runner.sh; boleh diubah, lalu jalankan ulang docker compose.\n' "$NAME"
            grep -v -e '^RUNNER_NAME=' -e '^LUMBUNG_AGE_RECIPIENTS=' "$WORK/session"
        } > "$DIR/.env.tmp"
    fi
    # Variabel yang diberikan selalu menang, juga saat mengulang setelah gagal di pemeriksaan kunci.
    if [ -n "$SCRATCH_OVERRIDE" ]; then
        sed -i "s|^SCRATCH_DIR=.*|SCRATCH_DIR=$SCRATCH_OVERRIDE|" "$DIR/.env.tmp"
    fi
    if [ -n "$KEY_OVERRIDE" ]; then
        sed -i "s|^AGE_IDENTITY_FILE=.*|AGE_IDENTITY_FILE=$KEY_OVERRIDE|" "$DIR/.env.tmp"
    fi
    chmod 600 "$DIR/.env.tmp"
    mv "$DIR/.env.tmp" "$DIR/.env"

    SCRATCH=$(env_value SCRATCH_DIR "$DIR/.env")
    KEY=$(env_value AGE_IDENTITY_FILE "$DIR/.env")
    IMAGE=$(env_value MARIADB_IMAGE "$DIR/.env")
    check_path SCRATCH_DIR "$SCRATCH"
    check_path AGE_IDENTITY_FILE "$KEY"
    [ -n "$IMAGE" ] || die "MARIADB_IMAGE kosong di $DIR/.env."
}

# Kunci yang hilang tidak boleh sampai ke docker: bind mount ke path yang tidak ada membuat folder kosong di sana.
check_key() {
    step "Memeriksa kunci age di $KEY"
    if ! [ -f "$KEY" ] && ! { [ -n "$SUDO" ] && sudo test -f "$KEY"; }; then
        die "Kunci privat age runner tidak ada di $KEY. Salin kunci runner (yang public key-nya ada di BACKUP_AGE_RECIPIENTS) ke sana dengan izin 600, lalu jalankan perintah yang sama lagi. Lokasi lain: tambahkan AGE_IDENTITY_FILE=/path di depan sh, atau ubah $DIR/.env."
    fi
    # age-keygen dijalankan dari image MariaDB, sama seperti runner memakai age; image-nya sekaligus terunduh.
    public=$(docker run --rm --network none --entrypoint age-keygen -v "$KEY:/keys/age.key:ro" "$IMAGE" -y /keys/age.key) \
        || die "Tidak bisa membaca $KEY sebagai kunci age, atau image $IMAGE tidak bisa diunduh."
    if [ -z "$RECIPIENTS" ]; then
        warn "BACKUP_AGE_RECIPIENTS di LumbungDB kosong; kecocokan kunci tidak bisa diperiksa."
        return
    fi
    case ",$RECIPIENTS," in
        *",$public,"*) ;;
        *) die "Kunci di $KEY ($public) bukan penerima enkripsi backup. Pakai kunci runner yang public key-nya ada di BACKUP_AGE_RECIPIENTS LumbungDB." ;;
    esac
    mode=$(as_root stat -c '%a' "$KEY" 2>/dev/null || true)
    case "$mode" in ''|600|400) ;; *) warn "Izin $KEY adalah $mode; sebaiknya 600 (chmod 600 $KEY)." ;; esac
}

prepare_scratch() {
    step "Menyiapkan scratch di $SCRATCH"
    as_root mkdir -p "$SCRATCH" || die "Tidak bisa membuat $SCRATCH."
    as_root chmod 700 "$SCRATCH" || true
    free_kb=$(df -Pk "$SCRATCH" 2>/dev/null | awk 'NR == 2 {print $4}')
    if [ -n "$free_kb" ]; then printf '    bebas %s GB\n' "$((free_kb / 1024 / 1024))"; fi
}

start_runner() {
    step "Mengambil compose dan menjalankan runner"
    curl -fsS --max-time 30 -o "$WORK/compose.yml" "$BASE_URL/install/docker-compose.runner.yml" \
        || die "Tidak bisa mengunduh compose dari $BASE_URL."
    mv "$WORK/compose.yml" "$DIR/docker-compose.yml"
    rm -f "$DIR/docker-compose.runner.yml"
    docker compose -p "$PROJECT" -f "$DIR/docker-compose.yml" --env-file "$DIR/.env" up -d \
        || die "docker compose gagal. Perbaiki penyebabnya, lalu jalankan perintah yang sama lagi."
}

wait_running() {
    step "Memeriksa runner berjalan"
    sleep 10
    id=$(docker compose -p "$PROJECT" -f "$DIR/docker-compose.yml" --env-file "$DIR/.env" ps -q runner)
    running=$(docker inspect -f '{{.State.Running}} {{.State.Restarting}}' "$id" 2>/dev/null || true)
    if [ "$running" != "true false" ]; then
        docker logs --tail 20 "$id" >&2 2>&1 || true
        die "Runner tidak berjalan. Log lengkap: ${SUDO:+sudo }docker compose -p $PROJECT -f $DIR/docker-compose.yml logs"
    fi
}

report() {
    cat <<EOF

Runner $NAME terpasang.
  Folder      : $DIR
  Scratch     : $SCRATCH
  Kunci age   : $KEY
  Image       : $(env_value RUNNER_IMAGE "$DIR/.env")

Ubah .env bila perlu, lalu jalankan:
  cd $DIR && ${SUDO:+sudo }docker compose up -d

Runner mengirim heartbeat dalam satu menit. Pantau di $BASE_URL/runners
EOF
}

main() {
    BASE_URL=${LUMBUNG_URL:-}
    BASE_URL=${BASE_URL%/}
    TOKEN=${INSTALL_TOKEN:-}
    SCRATCH_OVERRIDE=${SCRATCH_DIR:-}
    KEY_OVERRIDE=${AGE_IDENTITY_FILE:-}
    # Variabel shell mengalahkan --env-file di compose, jadi jangan sampai terbawa.
    unset LUMBUNG_URL INSTALL_TOKEN SCRATCH_DIR AGE_IDENTITY_FILE LUMBUNG_RUNNER_TOKEN RUNNER_IMAGE MARIADB_IMAGE 2>/dev/null || true
    case "$BASE_URL" in http://*|https://*) ;; *) die "Isi LUMBUNG_URL, mis. https://lumbung.contoh.id" ;; esac
    case "$TOKEN" in lmn_*) ;; *) die "Isi INSTALL_TOKEN dari halaman Tambah runner." ;; esac
    case "$TOKEN$BASE_URL" in *[!A-Za-z0-9_:/.-]*) die "LUMBUNG_URL atau INSTALL_TOKEN berisi karakter yang tidak dikenal." ;; esac
    [ -z "$SCRATCH_OVERRIDE" ] || check_path SCRATCH_DIR "$SCRATCH_OVERRIDE"
    [ -z "$KEY_OVERRIDE" ] || check_path AGE_IDENTITY_FILE "$KEY_OVERRIDE"

    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    trap 'exit 130' INT TERM

    check_server
    request_session
    choose_dir
    write_env
    check_key
    prepare_scratch
    start_runner
    wait_running
    report
}

# Dipanggil di baris terakhir: unduhan yang terpotong tidak menjalankan apa pun. Perintah di dalamnya tidak boleh
# membaca stdin, karena stdin sh adalah isi script ini.
main </dev/null
