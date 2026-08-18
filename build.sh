#!/usr/bin/env bash
# Build the static archive and package it as a servable container image.
#
# Run this on the build server, which holds the database dump and the removal
# list. Neither is in this repository.
#
#   ./build.sh path/to/qanda-YYYY-MM-DD.sql.gz [image-tag]
#
# Produces ./site (generated HTML) and a container image, by default
# qanda-static:latest.
set -euo pipefail

DUMP=${1:-}
TAG=${2:-qanda-static:latest}

if [[ -z "$DUMP" || ! -f "$DUMP" ]]; then
  echo "usage: $0 <database-dump.sql.gz> [image-tag]" >&2
  echo "the dump is private and is never committed to this repository" >&2
  exit 2
fi

# Compose treats a bare filename as a *named volume*, so always pass a full path.
# Compose ships either as a docker plugin ("docker compose") or as a standalone
# binary ("docker-compose"); the build server has the latter.
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "neither 'docker compose' nor 'docker-compose' is available" >&2
  exit 3
fi

export QANDA_DUMP="$(cd "$(dirname "$DUMP")" && pwd)/$(basename "$DUMP")"
export HOST_UID="$(id -u)" HOST_GID="$(id -g)"

# A database container left behind by an interrupted run already has an
# initialised data directory, and MariaDB only imports the dump into an empty
# one, so always start from a clean database.
echo "==> clearing any previous build containers"
$COMPOSE --profile build down -v --remove-orphans >/dev/null 2>&1 || true

echo "==> building the generator images"
# Build every build-profile image up front: "up --build <service>" only builds the
# named service, leaving its dependencies without an image on a clean machine.
$COMPOSE --profile build build

echo "==> capturing images and rendering site from $DUMP"
$COMPOSE --profile build up --exit-code-from generate generate

echo "==> removing the throwaway database"
$COMPOSE --profile build down -v

echo "==> building image $TAG"
docker build -f Dockerfile.serve -t "$TAG" .

echo
echo "Built $TAG"
echo "Run it with:  docker run --rm -p 8088:80 $TAG"
