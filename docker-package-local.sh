#!/usr/bin/env bash
# 构建镜像并打包为可离线部署的压缩包（不含 MySQL/Redis、不含 ASR 模型）
#
# 用法：
#   ./docker-package-local.sh
#   VERSION=1.0.0 ./docker-package-local.sh
#   TARGET=server ./docker-package-local.sh           # 仅打包 xiaozhi-server 镜像
#   TARGET=web ./docker-package-local.sh              # 仅打包 manager-web+api 镜像
#   BUILD_BASE=true ./docker-package-local.sh
#   PULL_BASE=false ./docker-package-local.sh           # 跳过拉取，本地构建 server-base
#   GHCR_MIRROR=ghcr.1ms.run ./docker-package-local.sh  # 指定国内镜像代理（默认）
#   SKIP_BUILD=true VERSION=1.0.0 ./docker-package-local.sh  # 仅打包已有镜像
#
# ASR 模型（model.pt，约 900MB）不包含在压缩包内，部署时单独执行 ./download-model.sh
#
# 输出：dist/xiaozhi-deploy-<VERSION>.tar.gz

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
mkdir -p "${ROOT_DIR}/dist"

IMAGE_PREFIX="${IMAGE_PREFIX:-xiaozhi-local}"
VERSION="${VERSION:-$(date +%Y%m%d)}"
TARGET="${TARGET:-all}"
BUILD_BASE="${BUILD_BASE:-false}"
SKIP_BUILD="${SKIP_BUILD:-false}"
OUTPUT_DIR="${OUTPUT_DIR:-${ROOT_DIR}/dist}"
mkdir -p "${OUTPUT_DIR}"

SERVER_IMAGE="${IMAGE_PREFIX}/xiaozhi-esp32-server:${VERSION}"
WEB_IMAGE="${IMAGE_PREFIX}/xiaozhi-esp32-server-web:${VERSION}"
PACKAGE_NAME="xiaozhi-deploy-${VERSION}"
STAGING_DIR="${OUTPUT_DIR}/${PACKAGE_NAME}"
ARCHIVE_PATH="${OUTPUT_DIR}/${PACKAGE_NAME}.tar.gz"

MODEL_URL="https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt"

case "${TARGET}" in
  all|server|web) ;;
  *)
    echo "错误: TARGET 必须是 all、server 或 web，当前为: ${TARGET}"
    exit 1
    ;;
esac

echo "==> 打包配置"
echo "    版本:         ${VERSION}"
echo "    打包目标:     ${TARGET}"
echo "    Server 镜像:  ${SERVER_IMAGE}"
echo "    Web 镜像:     ${WEB_IMAGE}"
echo "    输出目录:     ${OUTPUT_DIR}"
echo "    ASR 模型:     不包含（部署时单独 download-model.sh）"
echo "    跳过构建:     ${SKIP_BUILD}"
echo

if [[ "${SKIP_BUILD}" != "true" ]]; then
  BUILD_BASE="${BUILD_BASE}" IMAGE_PREFIX="${IMAGE_PREFIX}" VERSION="${VERSION}" \
    TARGET="${TARGET}" PULL_BASE="${PULL_BASE:-true}" GHCR_MIRROR="${GHCR_MIRROR:-ghcr.1ms.run}" \
    "${ROOT_DIR}/docker-build-local.sh"
else
  echo "==> 跳过镜像构建，使用本地已有镜像"
  if [[ "${TARGET}" == "all" || "${TARGET}" == "server" ]]; then
    docker image inspect "${SERVER_IMAGE}" >/dev/null
  fi
  if [[ "${TARGET}" == "all" || "${TARGET}" == "web" ]]; then
    docker image inspect "${WEB_IMAGE}" >/dev/null
  fi
fi

echo
echo "==> 准备打包目录: ${STAGING_DIR}"
rm -rf "${STAGING_DIR}"
mkdir -p "${STAGING_DIR}/images" "${STAGING_DIR}/data" "${STAGING_DIR}/models/SenseVoiceSmall" "${STAGING_DIR}/uploadfile"

echo "==> 导出 Docker 镜像..."
if [[ "${TARGET}" == "all" || "${TARGET}" == "server" ]]; then
  docker save -o "${STAGING_DIR}/images/xiaozhi-esp32-server.tar" "${SERVER_IMAGE}"
fi
if [[ "${TARGET}" == "all" || "${TARGET}" == "web" ]]; then
  docker save -o "${STAGING_DIR}/images/xiaozhi-esp32-server-web.tar" "${WEB_IMAGE}"
fi

echo "==> 复制 compose 与配置模板..."
cat > "${STAGING_DIR}/docker-compose.yml" <<'COMPOSE_EOF'
# 小智服务端本地部署（使用已有 MySQL / Redis）
# 首次部署请执行 ./install.sh

services:
  xiaozhi-esp32-server:
    image: ${DOCKER_IMAGE_SERVER}
    container_name: xiaozhi-esp32-server
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "${SERVER_WS_PORT:-8000}:8000"
      - "${SERVER_HTTP_PORT:-8003}:8003"
    security_opt:
      - seccomp:unconfined
    environment:
      TZ: ${TZ:-Asia/Shanghai}
    volumes:
      - ./data:/opt/xiaozhi-esp32-server/data
      # 挂目录而非单文件，避免宿主机缺少 model.pt 时被 Docker 建成空目录
      - ./models/SenseVoiceSmall:/opt/xiaozhi-esp32-server/models/SenseVoiceSmall

  xiaozhi-esp32-server-web:
    image: ${DOCKER_IMAGE_WEB}
    container_name: xiaozhi-esp32-server-web
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "${WEB_PORT:-8002}:8002"
    environment:
      TZ: ${TZ:-Asia/Shanghai}
      SPRING_DATASOURCE_DRUID_URL: jdbc:mysql://${MYSQL_HOST:-host.docker.internal}:${MYSQL_PORT:-3306}/${MYSQL_DATABASE:-xiaozhi_esp32_server}?useUnicode=true&characterEncoding=UTF-8&serverTimezone=Asia/Shanghai&nullCatalogMeansCurrent=true&connectTimeout=30000&socketTimeout=30000&autoReconnect=true&failOverReadOnly=false&maxReconnects=10
      SPRING_DATASOURCE_DRUID_USERNAME: ${MYSQL_USER:-root}
      SPRING_DATASOURCE_DRUID_PASSWORD: ${MYSQL_PASSWORD:-123456}
      SPRING_DATA_REDIS_HOST: ${REDIS_HOST:-host.docker.internal}
      SPRING_DATA_REDIS_PORT: ${REDIS_PORT:-6379}
      SPRING_DATA_REDIS_PASSWORD: ${REDIS_PASSWORD:-}
    volumes:
      - ./uploadfile:/uploadfile
COMPOSE_EOF

cat > "${STAGING_DIR}/.env.example" <<ENV_EOF
# 复制为 .env 后修改：cp .env.example .env

DOCKER_IMAGE_SERVER=${SERVER_IMAGE}
DOCKER_IMAGE_WEB=${WEB_IMAGE}

SERVER_WS_PORT=8000
SERVER_HTTP_PORT=8003
WEB_PORT=8002
TZ=Asia/Shanghai

MYSQL_HOST=host.docker.internal
MYSQL_PORT=3306
MYSQL_DATABASE=xiaozhi_esp32_server
MYSQL_USER=root
MYSQL_PASSWORD=12345678

REDIS_HOST=host.docker.internal
REDIS_PORT=6379
REDIS_PASSWORD=
ENV_EOF

cat > "${STAGING_DIR}/data/.config.yaml.example" <<'CONFIG_EOF'
server:
  ip: 0.0.0.0
  port: 8000
  http_port: 8003
  vision_explain: http://你的ip或者域名:8003/mcp/vision/explain
manager-api:
  url: http://xiaozhi-esp32-server-web:8002/xiaozhi
  secret: 你的server.secret值
prompt_template: agent-base-prompt.txt
CONFIG_EOF

cat > "${STAGING_DIR}/models/SenseVoiceSmall/README.txt" <<README_MODEL
ASR 语音识别模型（约 900MB）不包含在部署压缩包内，首次部署请执行：

  ./download-model.sh

模型将下载到：

  models/SenseVoiceSmall/model.pt

下载地址：
  ${MODEL_URL}

模型通常只需下载一次，后续升级部署包时可直接复用此文件。
README_MODEL

cat > "${STAGING_DIR}/download-model.sh" <<DOWNLOAD_EOF
#!/usr/bin/env bash
# 一次性下载 ASR 模型（约 900MB），不包含在部署压缩包内
set -euo pipefail

DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
MODEL_PATH="\${DIR}/models/SenseVoiceSmall/model.pt"
MODEL_URL="${MODEL_URL}"

mkdir -p "\$(dirname "\${MODEL_PATH}")"

if [[ -f "\${MODEL_PATH}" ]]; then
  echo "ASR 模型已存在，跳过下载: \${MODEL_PATH}"
  ls -lh "\${MODEL_PATH}"
  exit 0
fi

echo "==> 下载 ASR 模型（约 900MB）..."
echo "    目标: \${MODEL_PATH}"
echo "    来源: \${MODEL_URL}"
curl -fL --progress-bar "\${MODEL_URL}" -o "\${MODEL_PATH}" || {
  echo "错误: 下载失败，请检查网络或手动下载后放到 \${MODEL_PATH}"
  exit 1
}

echo "==> 下载完成"
ls -lh "\${MODEL_PATH}"
DOWNLOAD_EOF

chmod +x "${STAGING_DIR}/download-model.sh"

cat > "${STAGING_DIR}/install.sh" <<INSTALL_EOF
#!/usr/bin/env bash
set -euo pipefail

DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
cd "\$DIR"

MODEL_PATH="\${DIR}/models/SenseVoiceSmall/model.pt"

echo "==> [1/5] 检查 Docker..."
command -v docker >/dev/null || { echo "错误: 未安装 Docker"; exit 1; }
docker compose version >/dev/null 2>&1 || docker-compose version >/dev/null || { echo "错误: 未安装 Docker Compose"; exit 1; }

echo "==> [2/5] 导入镜像..."
shopt -s nullglob
tars=(images/*.tar)
if [[ \${#tars[@]} -eq 0 ]]; then
  echo "错误: images/ 目录下未找到镜像 tar 包"
  exit 1
fi
for tar in "\${tars[@]}"; do
  echo "    导入: \${tar}"
  docker load -i "\${tar}"
done
shopt -u nullglob

echo "==> [3/5] 准备配置..."
mkdir -p data uploadfile models/SenseVoiceSmall

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "    已生成 .env，请编辑 MySQL/Redis 连接信息后重新执行: ./install.sh"
  exit 0
fi

if [[ ! -f data/.config.yaml ]]; then
  cp data/.config.yaml.example data/.config.yaml
  echo "    已生成 data/.config.yaml，启动 web 后请填写 manager-api.secret"
fi

echo "==> [4/5] 检查 ASR 模型..."
if [[ ! -f "\${MODEL_PATH}" ]]; then
  echo "错误: 未找到 ASR 模型 \${MODEL_PATH}"
  echo "请先执行: ./download-model.sh"
  echo "（模型约 900MB，只需下载一次，不包含在部署压缩包内）"
  exit 1
fi
echo "    已存在 ASR 模型"

echo "==> [5/5] 启动服务..."
docker compose up -d

echo
echo "部署完成。"
echo "  智控台: http://localhost:\$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 || echo 8002)"
echo "  首次请注册管理员，在【参数管理】复制 server.secret 到 data/.config.yaml"
echo "  然后重启 server: docker restart xiaozhi-esp32-server"
echo
echo "查看日志:"
echo "  docker logs -f -n 50 xiaozhi-esp32-server"
echo "  docker logs -f -n 50 xiaozhi-esp32-server-web"
INSTALL_EOF

cat > "${STAGING_DIR}/stop.sh" <<'STOP_EOF'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
docker compose down
echo "服务已停止。"
STOP_EOF

cat > "${STAGING_DIR}/README-DEPLOY.md" <<README_EOF
# 小智服务端离线部署包

版本: ${VERSION}

## 包含内容

- \`images/\` — Docker 镜像 tar（xiaozhi-server、manager-web+manager-api）
- \`docker-compose.yml\` — 服务编排（不含 MySQL/Redis）
- \`.env.example\` — 环境变量模板
- \`data/.config.yaml.example\` — xiaozhi-server 远程配置模板
- \`download-model.sh\` — ASR 模型一次性下载（约 900MB，**不含在压缩包内**）
- \`install.sh\` / \`stop.sh\` — 安装与停止脚本

## 前置条件

1. 目标机器已安装 Docker 与 Docker Compose
2. 已有 MySQL、Redis（端口映射到宿主机，默认 3306 / 6379）
3. MySQL 中已创建数据库 \`xiaozhi_esp32_server\`（或由应用首次连接前创建）

## 快速部署

\`\`\`bash
tar -xzf ${PACKAGE_NAME}.tar.gz
cd ${PACKAGE_NAME}

# 1. 首次部署：下载 ASR 模型（只需一次，升级时可跳过）
./download-model.sh

# 2. 配置并启动
cp .env.example .env
# 编辑 .env 中的 MYSQL_PASSWORD、REDIS 等
./install.sh
\`\`\`

> **说明**：ASR 模型（\`model.pt\`）体积较大，已从压缩包剥离。首次部署运行 \`download-model.sh\`，后续升级部署包时保留 \`models/\` 目录即可复用。

## 首次配置流程

1. 浏览器打开 http://<服务器IP>:8002
2. 注册第一个账号（自动成为管理员）
3. 【参数管理】→ 复制 \`server.secret\`
4. 编辑 \`data/.config.yaml\`，填入 \`manager-api.secret\`
5. \`docker restart xiaozhi-esp32-server\`

## 端口

| 端口 | 服务 |
|------|------|
| 8000 | WebSocket 语音 |
| 8002 | 智控台 |
| 8003 | HTTP / 视觉分析 |

## 停止服务

\`\`\`bash
./stop.sh
\`\`\`
README_EOF

chmod +x "${STAGING_DIR}/install.sh" "${STAGING_DIR}/stop.sh"

echo "==> 生成压缩包: ${ARCHIVE_PATH}"
mkdir -p "${OUTPUT_DIR}"
tar -czf "${ARCHIVE_PATH}" -C "${OUTPUT_DIR}" "${PACKAGE_NAME}"

PACKAGE_SIZE="$(du -h "${ARCHIVE_PATH}" | cut -f1)"
echo
echo "==> 打包完成"
echo "    压缩包: ${ARCHIVE_PATH}"
echo "    大小:   ${PACKAGE_SIZE}"
echo "    解压后: cd ${PACKAGE_NAME} && ./download-model.sh && cp .env.example .env && ./install.sh"
echo
ls -lh "${ARCHIVE_PATH}"
