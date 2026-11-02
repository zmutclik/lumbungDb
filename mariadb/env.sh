#!/bin/bash
# Membuat .env dari env.sample dengan user & password acak
set -e

if [ -f ./.env ] && [ "$1" != "--force" ]; then
    echo ".env sudah ada. Password di dalamnya mungkin sudah dipakai database." >&2
    echo "Jalankan '$0 --force' untuk menimpanya." >&2
    exit 1
fi

gen_pass() {
    openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 32
}

if [[ "$OSTYPE" == "darwin"* ]]; then
    SED_INPLACE=(sed -i '')
else
    SED_INPLACE=(sed -i)
fi

cp ./env.sample ./.env
"${SED_INPLACE[@]}" \
    -e "/^DB_ROOTPASSWORD=/s/=.*/=$(gen_pass)/" \
    -e "/^DB_APPUSER=/s/=.*/=user_$(openssl rand -hex 4)/" \
    -e "/^DB_APPPASS=/s/=.*/=$(gen_pass)/" \
    -e "/^BACKUP_PASS=/s/=.*/=$(gen_pass)/" \
    ./.env
chmod 600 ./.env

echo ".env berhasil dibuat"
