#!/bin/bash
# Build & push image. Tag berisi versi MariaDB dan versi agen, tanpa latest:
#   MARIADB_VERSION=11.4 AGENT_VERSION=a1 ./build-image.sh   # zmutclik/mariadb:lumbungdb-11.4-a1
# PUSH=0 hanya build lokal.
set -e

MARIADB_VERSION="${MARIADB_VERSION:-12.3}"
AGENT_VERSION="${AGENT_VERSION:-a1}"
IMAGE="${IMAGE:-zmutclik/mariadb}"
TAG="$MARIADB_VERSION-$AGENT_VERSION"

docker build --pull \
    --build-arg MARIADB_VERSION="$MARIADB_VERSION" \
    --build-arg IMAGE_TAG="$TAG" \
    -t "$IMAGE:lumbungdb-$TAG" \
    .

if [ "${PUSH:-1}" = "1" ]; then
    docker push "$IMAGE:lumbungdb-$TAG"
fi
