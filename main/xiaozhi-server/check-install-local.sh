#!/usr/bin/env bash
# 源码部署启动前检查 / 安装（配合 docker-compose.local.yml）
# 适用：Ubuntu 24（亦兼容其他带 apt 的 Debian 系）
#
# 用法（在 main/xiaozhi-server 目录执行）：
#   ./check-install-local.sh
#   ./check-install-local.sh --check-only      # 只检查，不安装/下载
#   ./check-install-local.sh --skip-model      # 不下载 ASR 模型
#   ./check-install-local.sh --skip-docker     # 不自动安装 Docker
#   ./check-install-local.sh --build           # 检查通过后构建镜像
#
# 通过后启动：
#   docker compose -f docker-compose.local.yml up -d

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${SCRIPT_DIR}"

CHECK_ONLY=false
SKIP_MODEL=false
SKIP_DOCKER=false
DO_BUILD=false
MODEL_URL="${MODEL_URL:-https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt}"
MODEL_PATH="${SCRIPT_DIR}/models/SenseVoiceSmall/model.pt"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.local.example"
CONFIG_FILE="${SCRIPT_DIR}/data/.config.yaml"
CONFIG_EXAMPLE="${SCRIPT_DIR}/config_from_api.yaml"

FAILS=0
WARNS=0

for arg in "$@"; do
  case "${arg}" in
    --check-only) CHECK_ONLY=true ;;
    --skip-model) SKIP_MODEL=true ;;
    --skip-docker) SKIP_DOCKER=true ;;
    --build) DO_BUILD=true ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数: ${arg}（可用 --help）"
      exit 1
      ;;
  esac
done

ok()   { echo "  [OK]  $*"; }
warn() { echo "  [WARN] $*"; WARNS=$((WARNS + 1)); }
fail() { echo "  [FAIL] $*"; FAILS=$((FAILS + 1)); }
info() { echo "  -> $*"; }

load_env() {
  if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
  fi
}

echo "==> 源码部署启动前检查"
echo "    目录: ${SCRIPT_DIR}"
echo "    模式: $([ "${CHECK_ONLY}" = true ] && echo check-only || echo check+install)"
echo

# ---------- 系统 ----------
echo "==> [1/7] 系统"
if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  ok "${NAME:-Linux} ${VERSION_ID:-}"
  if [[ "${ID:-}" != "ubuntu" ]]; then
    warn "非 Ubuntu（当前 ${ID:-unknown}），自动装 Docker 可能不可用，请自行确保 Docker 已安装"
  fi
else
  warn "无法识别发行版，请自行确认 Docker / 网络环境"
fi

# ---------- Docker ----------
echo
echo "==> [2/7] Docker"

install_docker_ubuntu() {
  info "安装 Docker CE + Compose 插件..."
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  sudo systemctl enable --now docker
  if ! groups "${USER}" | grep -q '\bdocker\b'; then
    sudo usermod -aG docker "${USER}"
    warn "已将 ${USER} 加入 docker 组，请重新登录后再执行 docker（或临时用 sudo）"
  fi
}

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "Docker 可用: $(docker --version | head -1)"
  else
    fail "已安装 Docker 但当前用户无法访问 daemon（尝试: sudo usermod -aG docker \$USER 后重新登录）"
  fi
else
  if [[ "${CHECK_ONLY}" == true ]] || [[ "${SKIP_DOCKER}" == true ]]; then
    fail "未安装 Docker"
  else
    if [[ -f /etc/os-release ]]; then
      # shellcheck disable=SC1091
      . /etc/os-release
      if [[ "${ID:-}" == "ubuntu" ]]; then
        install_docker_ubuntu
        if docker info >/dev/null 2>&1; then
          ok "Docker 安装完成: $(docker --version | head -1)"
        else
          fail "Docker 已安装，但当前 shell 无权限；请重新登录后再跑本脚本"
        fi
      else
        fail "非 Ubuntu，请手动安装 Docker 后重试"
      fi
    else
      fail "请手动安装 Docker"
    fi
  fi
fi

if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    ok "Compose: $(docker compose version | head -1)"
  else
    fail "缺少 docker compose 插件（Ubuntu: sudo apt install docker-compose-plugin）"
  fi
fi

# ---------- 目录与配置 ----------
echo
echo "==> [3/7] 目录与 .env"

ensure_dirs() {
  mkdir -p "${SCRIPT_DIR}/data" \
    "${SCRIPT_DIR}/models/SenseVoiceSmall" \
    "${SCRIPT_DIR}/uploadfile"
}

if [[ "${CHECK_ONLY}" == true ]]; then
  [[ -d data ]] && ok "data/" || fail "缺少 data/"
  [[ -d models/SenseVoiceSmall ]] && ok "models/SenseVoiceSmall/" || fail "缺少 models/SenseVoiceSmall/"
  [[ -d uploadfile ]] && ok "uploadfile/" || fail "缺少 uploadfile/"
else
  ensure_dirs
  ok "已确保 data / models/SenseVoiceSmall / uploadfile"
fi

if [[ -f "${ENV_FILE}" ]]; then
  ok ".env 已存在"
else
  if [[ "${CHECK_ONLY}" == true ]]; then
    fail "缺少 .env（请: cp .env.local.example .env 并修改 MySQL/Redis）"
  else
    if [[ -f "${ENV_EXAMPLE}" ]]; then
      cp "${ENV_EXAMPLE}" "${ENV_FILE}"
      warn "已从 .env.local.example 生成 .env，请务必修改 MYSQL_PASSWORD / REDIS 等后再启动"
    else
      fail "缺少 .env 且找不到 .env.local.example"
    fi
  fi
fi

if [[ -f "${CONFIG_FILE}" ]]; then
  ok "data/.config.yaml 已存在"
else
  if [[ "${CHECK_ONLY}" == true ]]; then
    warn "缺少 data/.config.yaml（启动 web 注册后需填 manager-api.secret）"
  else
    if [[ -f "${CONFIG_EXAMPLE}" ]]; then
      cp "${CONFIG_EXAMPLE}" "${CONFIG_FILE}"
      # docker 内 server 访问 web 用服务名更稳
      if grep -q 'url: http://127.0.0.1:8002/xiaozhi' "${CONFIG_FILE}"; then
        sed -i.bak 's|url: http://127.0.0.1:8002/xiaozhi|url: http://xiaozhi-esp32-server-web:8002/xiaozhi|' "${CONFIG_FILE}" \
          && rm -f "${CONFIG_FILE}.bak"
      fi
      warn "已生成 data/.config.yaml，启动后请在智控台复制 server.secret 填入 manager-api.secret"
    else
      warn "缺少 config_from_api.yaml，跳过 .config.yaml 生成"
    fi
  fi
fi

load_env

# ---------- ASR 模型 ----------
echo
echo "==> [4/7] ASR 模型 (SenseVoiceSmall/model.pt)"

fix_model_pt_dir() {
  if [[ -d "${MODEL_PATH}" ]]; then
    if [[ -z "$(ls -A "${MODEL_PATH}" 2>/dev/null || true)" ]]; then
      if [[ "${CHECK_ONLY}" == true ]]; then
        fail "model.pt 是空目录（旧版文件挂载导致）。请: rmdir ${MODEL_PATH} 后重新下载"
      else
        rmdir "${MODEL_PATH}"
        warn "已删除误创建的空目录 model.pt"
      fi
    else
      fail "model.pt 是非空目录，请手动处理: ${MODEL_PATH}"
    fi
  fi
}

fix_model_pt_dir

if [[ -f "${MODEL_PATH}" ]]; then
  ok "模型文件存在 ($(du -h "${MODEL_PATH}" | cut -f1))"
elif [[ -d "${MODEL_PATH}" ]]; then
  : # 已在上面 fail
else
  if [[ "${SKIP_MODEL}" == true ]]; then
    warn "未找到 model.pt，已跳过下载（本地 FunASR 将不可用）"
  elif [[ "${CHECK_ONLY}" == true ]]; then
    fail "未找到 model.pt: ${MODEL_PATH}"
  else
    info "下载 ASR 模型（约 900MB）..."
    info "来源: ${MODEL_URL}"
    mkdir -p "$(dirname "${MODEL_PATH}")"
    if command -v curl >/dev/null 2>&1; then
      curl -fL --progress-bar "${MODEL_URL}" -o "${MODEL_PATH}.downloading"
      mv "${MODEL_PATH}.downloading" "${MODEL_PATH}"
      ok "下载完成 ($(du -h "${MODEL_PATH}" | cut -f1))"
    else
      fail "需要 curl 下载模型: sudo apt install -y curl"
    fi
  fi
fi

# ---------- 镜像 ----------
echo
echo "==> [5/7] Docker 镜像"

SERVER_IMAGE="${DOCKER_IMAGE_SERVER:-xiaozhi-local/xiaozhi-esp32-server:latest}"
WEB_IMAGE="${DOCKER_IMAGE_WEB:-xiaozhi-local/xiaozhi-esp32-server-web:latest}"

check_image() {
  local img="$1"
  if command -v docker >/dev/null 2>&1 && docker image inspect "${img}" >/dev/null 2>&1; then
    ok "镜像存在: ${img}"
  else
    fail "缺少镜像: ${img}"
  fi
}

need_build=false
if command -v docker >/dev/null 2>&1; then
  docker image inspect "${SERVER_IMAGE}" >/dev/null 2>&1 || need_build=true
  docker image inspect "${WEB_IMAGE}" >/dev/null 2>&1 || need_build=true
else
  need_build=true
fi

if [[ "${DO_BUILD}" == true ]]; then
  if [[ "${CHECK_ONLY}" == true ]]; then
    warn "--build 在 --check-only 下忽略"
    check_image "${SERVER_IMAGE}"
    check_image "${WEB_IMAGE}"
  else
    echo
    info "执行构建: ${ROOT_DIR}/docker-build-local.sh"
    "${ROOT_DIR}/docker-build-local.sh"
    check_image "${SERVER_IMAGE}"
    check_image "${WEB_IMAGE}"
  fi
else
  check_image "${SERVER_IMAGE}"
  check_image "${WEB_IMAGE}"
  if [[ "${need_build}" == true ]]; then
    warn "可在仓库根目录构建: ./docker-build-local.sh"
    warn "或对本脚本加 --build"
  fi
fi

# ---------- MySQL / Redis 连通性 ----------
echo
echo "==> [6/7] MySQL / Redis 连通性"

tcp_check() {
  local host="$1" port="$2" name="$3"
  # host.docker.internal 在宿主机上通常解析不到，改测 127.0.0.1
  local probe_host="${host}"
  if [[ "${host}" == "host.docker.internal" ]]; then
    probe_host="127.0.0.1"
  fi
  if (echo >/dev/tcp/"${probe_host}"/"${port}") >/dev/null 2>&1; then
    ok "${name} ${probe_host}:${port} 可达"
  else
    warn "${name} ${probe_host}:${port} 不可达（请确认服务已启动且端口映射到宿主机）"
  fi
}

MYSQL_HOST_V="${MYSQL_HOST:-host.docker.internal}"
MYSQL_PORT_V="${MYSQL_PORT:-3306}"
REDIS_HOST_V="${REDIS_HOST:-host.docker.internal}"
REDIS_PORT_V="${REDIS_PORT:-6379}"

tcp_check "${MYSQL_HOST_V}" "${MYSQL_PORT_V}" "MySQL"
tcp_check "${REDIS_HOST_V}" "${REDIS_PORT_V}" "Redis"

# ---------- Compose 文件 ----------
echo
echo "==> [7/7] Compose"
if [[ -f docker-compose.local.yml ]]; then
  ok "docker-compose.local.yml 存在"
else
  fail "缺少 docker-compose.local.yml"
fi

# ---------- 汇总 ----------
echo
echo "==> 结果: ${FAILS} 失败, ${WARNS} 警告"
if [[ "${FAILS}" -gt 0 ]]; then
  echo "请先处理 [FAIL] 项后再启动。"
  exit 1
fi

echo
echo "检查通过。启动命令："
echo "  cd ${SCRIPT_DIR}"
echo "  docker compose -f docker-compose.local.yml up -d"
echo
echo "首次配置："
echo "  1. 打开 http://<服务器IP>:${WEB_PORT:-8002} 注册管理员"
echo "  2. 【参数管理】复制 server.secret → 写入 data/.config.yaml 的 manager-api.secret"
echo "  3. docker restart xiaozhi-esp32-server"
echo
echo "日志："
echo "  docker logs -f -n 50 xiaozhi-esp32-server"
echo "  docker logs -f -n 50 xiaozhi-esp32-server-web"
