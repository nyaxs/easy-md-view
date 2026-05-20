#!/usr/bin/env bash
set -euo pipefail

APP_IMAGE="${MD_WORKSPACE_IMAGE:-ghcr.io/nyaxs/easy-md-view:latest}"
IMAGE_TAR="${IMAGE_TAR:-md-workspace-image.tar}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.deploy.yml}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but was not found."
  exit 1
fi

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    return 127
  fi
}

if ! compose_cmd version >/dev/null 2>&1; then
  echo "docker compose plugin or docker-compose is required but was not found."
  exit 1
fi

if [ -f "$IMAGE_TAR" ]; then
  echo "Loading image from $IMAGE_TAR ..."
  docker load -i "$IMAGE_TAR"
else
  echo "Image tar not found: $IMAGE_TAR"
  echo "Skip docker load and try to use existing local image: $APP_IMAGE"
fi

if ! docker image inspect "$APP_IMAGE" >/dev/null 2>&1; then
  echo "Image $APP_IMAGE does not exist. Put $IMAGE_TAR in this directory or load the image manually."
  exit 1
fi

echo "Starting Markdown workspace ..."
compose_cmd -f "$COMPOSE_FILE" up -d --force-recreate

PORT="${MD_WORKSPACE_PORT:-23333}"
echo "Done. Open: http://<server-ip>:$PORT/"
