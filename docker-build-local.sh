#!/usr/bin/env bash
# 本地构建 xiaozhi-server 与 manager-web/manager-api 镜像
#
# 用法：
#   ./docker-build-local.sh                         # 构建全部（base 准备 + server + web）
#   TARGET=server ./docker-build-local.sh           # 仅 xiaozhi-server
#   TARGET=web ./docker-build-local.sh              # 仅 manager-web + manager-api
#   TARGET=base ./docker-build-local.sh             # 仅 server-base（Python 依赖层）
#   IMAGE_PREFIX=myrepo VERSION=1.0.0 ./docker-build-local.sh
#   BUILD_BASE=true ./docker-build-local.sh         # 强制本地重建 Python 基础镜像
#   PULL_BASE=false ./docker-build-local.sh         # 跳过拉取，直接本地构建 base
#   GHCR_MIRROR=ghcr.nju.edu.cn ./docker-build-local.sh  # 更换镜像代理
#
# 部署（使用已有 MySQL/Redis）：
#   cd main/xiaozhi-server
#   cp .env.local.example .env   # 首次，并按本机修改
#   docker compose -f docker-compose.local.yml up -d

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

IMAGE_PREFIX="${IMAGE_PREFIX:-xiaozhi-local}"
VERSION="${VERSION:-latest}"
TARGET="${TARGET:-all}"
BUILD_BASE="${BUILD_BASE:-false}"
PULL_BASE="${PULL_BASE:-true}"
GHCR_MIRROR="${GHCR_MIRROR:-ghcr.1ms.run}"
SERVER_BASE_REPO="${SERVER_BASE_REPO:-xinnan-tech/xiaozhi-esp32-server:server-base}"
SERVER_BASE_PULL_IMAGE="${GHCR_MIRROR}/${SERVER_BASE_REPO}"
SERVER_BASE_IMAGE="${SERVER_BASE_IMAGE:-${SERVER_BASE_PULL_IMAGE}}"

SERVER_IMAGE="${IMAGE_PREFIX}/xiaozhi-esp32-server:${VERSION}"
WEB_IMAGE="${IMAGE_PREFIX}/xiaozhi-esp32-server-web:${VERSION}"

case "${TARGET}" in
  all|base|server|web) ;;
  *)
    echo "错误: TARGET 必须是 all、base、server 或 web，当前为: ${TARGET}"
    exit 1
    ;;
esac

echo "==> 构建配置"
echo "    项目根目录: ${ROOT_DIR}"
echo "    构建目标:     ${TARGET}"
echo "    Server 镜像:  ${SERVER_IMAGE}"
echo "    Web 镜像:     ${WEB_IMAGE}"
echo "    重建基础镜像: ${BUILD_BASE}"
echo "    拉取基础镜像: ${PULL_BASE}"
echo "    镜像代理:     ${GHCR_MIRROR}"
echo "    基础镜像:     ${SERVER_BASE_IMAGE}"
echo

ensure_server_base_image() {
  if docker image inspect "${SERVER_BASE_IMAGE}" >/dev/null 2>&1; then
    echo "    已存在基础镜像: ${SERVER_BASE_IMAGE}"
    return 0
  fi

  if [[ "${PULL_BASE}" == "true" ]]; then
    echo "    拉取基础镜像: ${SERVER_BASE_PULL_IMAGE}"
    if docker pull "${SERVER_BASE_PULL_IMAGE}"; then
      if [[ "${SERVER_BASE_PULL_IMAGE}" != "${SERVER_BASE_IMAGE}" ]]; then
        docker tag "${SERVER_BASE_PULL_IMAGE}" "${SERVER_BASE_IMAGE}"
        echo "    已标记为: ${SERVER_BASE_IMAGE}"
      fi
      return 0
    fi
    echo "    拉取失败，改为本地构建..."
  else
    echo "    已禁用拉取（PULL_BASE=false），本地构建基础镜像..."
  fi

  docker build -f Dockerfile-server-base -t "${SERVER_BASE_IMAGE}" .
}

build_base() {
  if [[ "${BUILD_BASE}" == "true" ]]; then
    echo "==> 构建 server 基础镜像 (Python 依赖)..."
    docker build -f Dockerfile-server-base -t "${SERVER_BASE_IMAGE}" .
  else
    echo "==> 准备 server 基础镜像..."
    ensure_server_base_image
  fi
}

build_server() {
  echo "==> 构建 xiaozhi-server 镜像..."
  echo "    会按当前 requirements.txt 补齐基础镜像缺少的依赖（含 mcp 2.x 的 httpx2）"
  docker build -f Dockerfile-server \
    --build-arg BASE_IMAGE="${SERVER_BASE_IMAGE}" \
    -t "${SERVER_IMAGE}" .
}

build_web() {
  echo "==> 构建 manager-web + manager-api 镜像..."
  docker build -f Dockerfile-web -t "${WEB_IMAGE}" .
}

case "${TARGET}" in
  base)
    build_base
    ;;
  server)
    build_base
    echo
    build_server
    ;;
  web)
    build_web
    ;;
  all)
    build_base
    echo
    build_server
    echo
    build_web
    ;;
esac

echo
echo "==> 构建完成"
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" \
  | grep -E "^(REPOSITORY|${IMAGE_PREFIX}/|${GHCR_MIRROR}/|ghcr)" || true

update_env_var() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}" && rm -f "${ENV_FILE}.bak"
  else
    echo "${key}=${value}" >> "${ENV_FILE}"
  fi
}

ENV_FILE="${ROOT_DIR}/main/xiaozhi-server/.env"
if [[ -f "${ENV_FILE}" ]]; then
  echo
  echo "==> 更新 ${ENV_FILE} 中的镜像名..."
  if [[ "${TARGET}" == "all" || "${TARGET}" == "server" ]]; then
    update_env_var "DOCKER_IMAGE_SERVER" "${SERVER_IMAGE}"
  fi
  if [[ "${TARGET}" == "all" || "${TARGET}" == "web" ]]; then
    update_env_var "DOCKER_IMAGE_WEB" "${WEB_IMAGE}"
  fi
else
  echo
  echo "提示: 尚未创建 main/xiaozhi-server/.env"
  echo "  cp main/xiaozhi-server/.env.local.example main/xiaozhi-server/.env"
  if [[ "${TARGET}" == "all" || "${TARGET}" == "server" ]]; then
    echo "    DOCKER_IMAGE_SERVER=${SERVER_IMAGE}"
  fi
  if [[ "${TARGET}" == "all" || "${TARGET}" == "web" ]]; then
    echo "    DOCKER_IMAGE_WEB=${WEB_IMAGE}"
  fi
fi

if [[ "${TARGET}" == "all" || "${TARGET}" == "server" ]]; then
  echo
  echo "==> 仅重启 server:"
  echo "  cd main/xiaozhi-server && docker compose -f docker-compose.local.yml up -d xiaozhi-esp32-server"
fi
if [[ "${TARGET}" == "all" || "${TARGET}" == "web" ]]; then
  echo
  echo "==> 仅重启 web:"
  echo "  cd main/xiaozhi-server && docker compose -f docker-compose.local.yml up -d xiaozhi-esp32-server-web"
fi
if [[ "${TARGET}" == "all" ]]; then
  echo
  echo "==> 启动全部:"
  echo "  cd main/xiaozhi-server && docker compose -f docker-compose.local.yml up -d"
fi
