#!/usr/bin/env bash
set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/nyaxs/easy-md-view:latest}"
IMAGE_TAR="${IMAGE_TAR:-md-workspace-image.tar}"
PLATFORM="${PLATFORM:-}"
BASE_IMAGE="${BASE_IMAGE:-python:3.11-slim-bullseye}"
APT_MIRROR="${APT_MIRROR:-https://mirrors.tuna.tsinghua.edu.cn/debian}"
APT_SECURITY_MIRROR="${APT_SECURITY_MIRROR:-https://mirrors.tuna.tsinghua.edu.cn/debian-security}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"
EXPORT_TAR="${EXPORT_TAR:-1}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but was not found."
  exit 1
fi

build_args=(
  --build-arg "BASE_IMAGE=$BASE_IMAGE"
  --build-arg "APT_MIRROR=$APT_MIRROR"
  --build-arg "APT_SECURITY_MIRROR=$APT_SECURITY_MIRROR"
  --build-arg "PIP_INDEX_URL=$PIP_INDEX_URL"
  --build-arg "PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST"
  -t "$IMAGE"
)

if [ -n "$PLATFORM" ]; then
  build_args=(--platform "$PLATFORM" "${build_args[@]}")
fi

echo "Building image: $IMAGE"
echo "Base image: $BASE_IMAGE"
echo "APT mirror: $APT_MIRROR"
echo "PIP index: $PIP_INDEX_URL"

docker build "${build_args[@]}" .

if [ "$EXPORT_TAR" = "1" ]; then
  echo "Exporting image to: $IMAGE_TAR"
  docker save -o "$IMAGE_TAR" "$IMAGE"
fi

echo "Done."
