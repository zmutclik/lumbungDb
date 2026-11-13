#!/bin/bash
# Build & push image runner. Tag = versi runner, tanpa latest:
#   RUNNER_VERSION=r2 ./build-image.sh     # lumbungdb/runner:r2
# PUSH=0 hanya build lokal. Sesudah rilis, naikkan RUNNER_VERSION_LATEST di LumbungDB supaya runner lama ditandai.
set -e
cd "$(dirname "$0")"

RUNNER_VERSION="${RUNNER_VERSION:?isi RUNNER_VERSION, mis. r2}"
IMAGE="${IMAGE:-lumbungdb/runner}"

docker build --pull --build-arg RUNNER_VERSION="$RUNNER_VERSION" -t "$IMAGE:$RUNNER_VERSION" .

if [ "${PUSH:-1}" = "1" ]; then
    docker push "$IMAGE:$RUNNER_VERSION"
fi
