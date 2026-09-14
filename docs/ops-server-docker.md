# 服务器源码 + Docker 运维手册

本文面向在 Linux 服务器上部署本仓库的运维人员，覆盖：**拉源码 → 准备依赖 → 构建并启动 Docker → 日常重启 / 升级**。

推荐部署方式是 **拉取本仓库源码，本地构建镜像**，再用 `docker-compose.local.yml` 启动。该方式：

- 容器内只跑 `xiaozhi-server`（Python 语音引擎）和 `xiaozhi-esp32-server-web`（智控台：manager-api + manager-web）
- **不内置 MySQL / Redis**，复用服务器上已有的数据库和缓存
- 配置、ASR 模型、上传文件都挂在宿主机目录，升级容器不会丢数据

> 若不想拉源码、只想用官方预编译镜像，且需要 compose 一并拉起 MySQL / Redis，请改看 [全模块 Docker 部署](./Deployment_all.md)。

---

## 1. 架构与端口

```
ESP32 设备 ──WebSocket──► :8000  xiaozhi-esp32-server
浏览器/管理员 ──HTTP──► :8002  xiaozhi-esp32-server-web（智控台）
设备 OTA / 视觉分析 ──HTTP──► :8003  xiaozhi-esp32-server
                              │
                              ├──► 宿主机 MySQL :3306
                              └──► 宿主机 Redis :6379
```

| 端口 | 容器 | 用途 | 注意 |
|------|------|------|------|
| 8000 | `xiaozhi-esp32-server` | 设备 WebSocket | 不要用浏览器打开 |
| 8002 | `xiaozhi-esp32-server-web` | 智控台 Web + API | 浏览器访问 |
| 8003 | `xiaozhi-esp32-server` | OTA / 视觉分析 HTTP | 全模块部署时 OTA 走 8002 |
| 3306 | 宿主机或已有容器 | MySQL | compose.local **不创建** |
| 6379 | 宿主机或已有容器 | Redis | compose.local **不创建** |

容器名固定为：

- `xiaozhi-esp32-server`
- `xiaozhi-esp32-server-web`

---

## 2. 环境要求

| 项 | 建议 |
|----|------|
| 系统 | Ubuntu 22.04 / 24.04（x86_64）。ARM64 需自行编译镜像 |
| 资源 | 全 API 约 2 核 4G；本地 FunASR 约 4 核 8G |
| 软件 | Git、Docker CE、Docker Compose 插件、curl |
| 依赖服务 | MySQL 8+、Redis 5+（端口映射到宿主机，或与 web 容器同网络） |
| 磁盘 | ASR 模型 `model.pt` 约 900MB，镜像构建另需数 GB |

以下命令默认工作目录：

```text
/opt/xiaozhi/xiaozhi-esp32-server
```

可按实际改路径，后文统一用 `$REPO` 表示仓库根目录。

---

## 3. 在服务器上拉源码

### 3.1 安装 Git（如尚未安装）

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates
```

### 3.2 克隆仓库

```bash
sudo mkdir -p /opt/xiaozhi
sudo chown "$USER":"$USER" /opt/xiaozhi
cd /opt/xiaozhi

# 本仓库
git clone https://github.com/phil-QS/xiaozhi-esp32-server.git

# 国内访问 GitHub 不稳定时可改用代理，例如：
# git clone https://ghproxy.net/https://github.com/phil-QS/xiaozhi-esp32-server.git
```

```bash
export REPO=/opt/xiaozhi/xiaozhi-esp32-server
cd "$REPO"
```

### 3.3 日常更新源码（不要覆盖本地配置）

`.env`、`data/.config.yaml`、`models/`、`uploadfile/` 已在 `.gitignore` 中，`git pull` 不会覆盖它们。

```bash
cd "$REPO"
git fetch --all
git status
git pull origin main
```

若本地改过已跟踪文件导致 pull 失败，先确认没有要保留的改动，再：

```bash
git stash push -u -m "ops-temp"
git pull origin main
```

**不要**对已上线目录执行 `git reset --hard`，以免丢掉未提交的运维改动。

---

## 4. 安装 Docker

Ubuntu 上可用仓库自带检查脚本自动安装：

```bash
cd "$REPO/main/xiaozhi-server"
./check-install-local.sh
```

也可手工安装：

```bash
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

把当前用户加入 `docker` 组后 **重新登录** SSH，再执行 `docker info` 确认无需 sudo。临时可用 `sudo docker ...`。

验证：

```bash
docker --version
docker compose version
```

---

## 5. 准备 MySQL 与 Redis

`docker-compose.local.yml` **不会**创建数据库容器。请先保证 MySQL、Redis 已运行，且从宿主机 `127.0.0.1` 能连上。

若本机还没有，可用 Docker 单独拉起（数据目录放在仓库外，避免被误删）：

```bash
# MySQL
docker run -d --name xiaozhi-esp32-server-db --restart unless-stopped \
  -e MYSQL_ROOT_PASSWORD=请改成强密码 \
  -e MYSQL_DATABASE=xiaozhi_esp32_server \
  -e MYSQL_INITDB_ARGS="--character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci" \
  -e TZ=Asia/Shanghai \
  -p 3306:3306 \
  -v /opt/xiaozhi/mysql-data:/var/lib/mysql \
  mysql:8

# Redis
docker run -d --name xiaozhi-esp32-server-redis --restart unless-stopped \
  -p 6379:6379 \
  redis:7
```

库名默认 `xiaozhi_esp32_server`。表结构由 manager-api 启动时通过 Liquibase 自动迁移，**升级一般不用手跑 SQL**。

---

## 6. 首次部署（推荐流程）

在 `$REPO/main/xiaozhi-server` 下操作。

### 6.1 一键检查 + 构建

```bash
cd "$REPO/main/xiaozhi-server"
chmod +x check-install-local.sh
./check-install-local.sh --build
```

脚本会：

1. 检查 / 安装 Docker
2. 创建 `data/`、`models/SenseVoiceSmall/`、`uploadfile/`
3. 从 `.env.local.example` 生成 `.env`（若不存在）
4. 从 `config_from_api.yaml` 生成 `data/.config.yaml`（Docker 下会把 manager-api 地址改成容器服务名）
5. 下载 ASR 模型 `models/SenseVoiceSmall/model.pt`（约 900MB，已存在则跳过）
6. 调用仓库根目录 `docker-build-local.sh` 构建镜像

只检查、不安装：

```bash
./check-install-local.sh --check-only
```

### 6.2 修改 `.env`

```bash
vi "$REPO/main/xiaozhi-server/.env"
```

至少改这些：

```bash
MYSQL_HOST=host.docker.internal   # 宿主机上的 MySQL；容器内通过 host-gateway 访问
MYSQL_PORT=3306
MYSQL_DATABASE=xiaozhi_esp32_server
MYSQL_USER=root
MYSQL_PASSWORD=与 MySQL 实际密码一致

REDIS_HOST=host.docker.internal
REDIS_PORT=6379
REDIS_PASSWORD=                   # 无密码留空
```

`host.docker.internal` 已在 compose 里映射到宿主机。MySQL/Redis 若在别的机器，改成对方 IP。

### 6.3 启动容器

```bash
cd "$REPO/main/xiaozhi-server"
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.local.yml ps
```

### 6.4 看启动日志

```bash
docker logs -f -n 80 xiaozhi-esp32-server-web
```

智控台起来的标志类似：

```text
xiaozhi.AdminApplication - Started AdminApplication in xx seconds
http://localhost:8002/xiaozhi/doc.html
```

此时 8000 端口的 server 可能仍报错（还没填 `server.secret`），先做第 7 节，再重启 server。

---

## 7. 首次配置（必须做完才能对话）

### 7.1 注册管理员

浏览器打开：`http://<服务器IP>:8002`

**第一个注册的账号是超级管理员**，后续账号是普通用户。

### 7.2 把 `server.secret` 写入配置

1. 用管理员登录智控台 → **参数管理**
2. 复制参数编码 `server.secret` 的参数值
3. 编辑宿主机文件：

```bash
vi "$REPO/main/xiaozhi-server/data/.config.yaml"
```

Docker 部署应类似：

```yaml
manager-api:
  url: http://xiaozhi-esp32-server-web:8002/xiaozhi
  secret: 此处粘贴 server.secret
```

`url` 必须用容器服务名 `xiaozhi-esp32-server-web`，不要写 `127.0.0.1`（那是容器自己，不是智控台）。

### 7.3 配置大模型密钥

智控台 → **模型配置** → **大语言模型**，给实际使用的模型填入 API Key（例如智谱）。

### 7.4 写入设备要连的地址

把 `<服务器IP>` 换成设备能访问到的地址（公网 IP 或内网 IP）：

| 参数编码 | 建议值 |
|----------|--------|
| `server.websocket` | `ws://<服务器IP>:8000/xiaozhi/v1/` |
| `server.ota` | `http://<服务器IP>:8002/xiaozhi/ota/` |

### 7.5 重启语音服务使 secret 生效

```bash
docker restart xiaozhi-esp32-server
docker logs -f -n 50 xiaozhi-esp32-server
```

成功标志类似：

```text
Websocket地址是      ws://x.x.x.x:8000/xiaozhi/v1/
=======上面的地址是websocket协议地址，请勿用浏览器访问=======
```

---

## 8. 日常启停与重启

所有 compose 命令都在 `main/xiaozhi-server` 目录执行：

```bash
cd "$REPO/main/xiaozhi-server"
```

### 8.1 查看状态

```bash
docker compose -f docker-compose.local.yml ps
docker ps --filter name=xiaozhi-esp32-server
```

### 8.2 启动（已存在则跳过，不存在则创建）

```bash
docker compose -f docker-compose.local.yml up -d
```

### 8.3 停止（容器删除，数据卷/挂载目录保留）

```bash
docker compose -f docker-compose.local.yml stop      # 只停止，容器还在
docker compose -f docker-compose.local.yml down      # 停止并删除容器
```

`down` **不会**删除宿主机上的 `data/`、`models/`、`uploadfile/`，也 **不会**删除你单独创建的 MySQL 数据目录。

### 8.4 重启（最常用）

改配置、改密钥、进程异常时：

```bash
# 只重启语音引擎（改 data/.config.yaml 后必做）
docker restart xiaozhi-esp32-server

# 只重启智控台（改 .env 里 MySQL/Redis 后建议用下面的 up，才能吃到新环境变量）
docker restart xiaozhi-esp32-server-web

# 两个一起重启
docker restart xiaozhi-esp32-server xiaozhi-esp32-server-web
```

注意：`docker restart` **不会**重新读取 `.env`。改了端口、镜像名、数据库连接后，要用：

```bash
docker compose -f docker-compose.local.yml up -d
```

只重建某一个服务：

```bash
docker compose -f docker-compose.local.yml up -d xiaozhi-esp32-server
docker compose -f docker-compose.local.yml up -d xiaozhi-esp32-server-web
```

### 8.5 重启后必看日志

```bash
docker logs -f -n 80 xiaozhi-esp32-server
docker logs -f -n 80 xiaozhi-esp32-server-web
```

Ctrl+C 只退出跟随，不会停容器。

### 8.6 重启 Docker 守护进程 / 整机开机

两个服务均设置了 `restart: unless-stopped`。Docker 服务恢复或机器重启后，容器会自动起来。确认：

```bash
sudo systemctl enable docker
docker compose -f docker-compose.local.yml ps
```

---

## 9. 升级（拉新代码 → 重建镜像 → 滚动重启）

```bash
cd "$REPO"
git pull origin main

# 构建新镜像（会写回 .env 里的 DOCKER_IMAGE_*）
./docker-build-local.sh

cd main/xiaozhi-server
docker compose -f docker-compose.local.yml up -d
```

只更新语音引擎或只更新智控台：

```bash
cd "$REPO"
TARGET=server ./docker-build-local.sh
cd main/xiaozhi-server && docker compose -f docker-compose.local.yml up -d xiaozhi-esp32-server

# 或
TARGET=web ./docker-build-local.sh
cd main/xiaozhi-server && docker compose -f docker-compose.local.yml up -d xiaozhi-esp32-server-web
```

升级注意：

1. **先备份** `main/xiaozhi-server/data/.config.yaml` 和 `.env`
2. 不要删 `models/SenseVoiceSmall/model.pt`，模型一般不用重下
3. 不要删 MySQL 数据目录
4. 智控台里的密钥、智能体配置在数据库里，重建镜像不会丢
5. 新版本若有新配置项，对照新的 `config_from_api.yaml` / `.env.local.example` **逐项补**，不要整文件覆盖旧配置

构建参数（可选）：

```bash
VERSION=20260914 ./docker-build-local.sh          # 打自定义标签
BUILD_BASE=true ./docker-build-local.sh           # 强制重建 Python 基础镜像
GHCR_MIRROR=ghcr.nju.edu.cn ./docker-build-local.sh
```

---

## 10. 日志、排障与常用命令

### 10.1 日志

```bash
docker logs -f -n 100 xiaozhi-esp32-server
docker logs -f -n 100 xiaozhi-esp32-server-web

# 最近 1 小时、带时间戳
docker logs --since 1h -t xiaozhi-esp32-server
```

宿主机上 Python 侧若写了文件日志，一般在容器内 / 挂载的 `data` 相关目录；优先看 `docker logs`。

### 10.2 进容器排查

```bash
docker exec -it xiaozhi-esp32-server bash
docker exec -it xiaozhi-esp32-server-web sh
```

### 10.3 资源与健康

```bash
docker stats xiaozhi-esp32-server xiaozhi-esp32-server-web
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8002
```

### 10.4 常见问题

| 现象 | 处理 |
|------|------|
| `permission denied` 访问 Docker | `sudo usermod -aG docker $USER` 后重新登录 |
| web 起不来 / 连不上库 | 核对 `.env` 的 MYSQL_*；宿主机 `127.0.0.1:3306` 能否连；改 `.env` 后必须 `compose up -d` 不能只 `restart` |
| server 日志报 manager-api 鉴权失败 | `data/.config.yaml` 的 `secret` 与智控台 `server.secret` 不一致，改完 `docker restart xiaozhi-esp32-server` |
| `model.pt` 变成目录 | 旧版把缺失文件挂成了空目录。`rmdir models/SenseVoiceSmall/model.pt` 后重新跑 `./check-install-local.sh` |
| 国内拉基础镜像失败 | `GHCR_MIRROR=ghcr.nju.edu.cn ./docker-build-local.sh` 或 `ghcr.1ms.run` |
| 端口被占用 | `ss -lntp \| grep -E '8000\|8002\|8003'`，改 `.env` 里对应端口后 `compose up -d` |
| 设备连不上 WebSocket | 防火墙放行 8000/8002/8003；智控台 `server.websocket` 必须是设备可达的 IP/域名，不能写 `127.0.0.1` |

防火墙示例（UFW）：

```bash
sudo ufw allow 8000/tcp
sudo ufw allow 8002/tcp
sudo ufw allow 8003/tcp
sudo ufw reload
```

公网部署务必限制智控台 8002 的来源 IP，并改掉 MySQL 默认密码。

---

## 11. 目录与数据备份

部署后宿主机关键路径（相对 `$REPO/main/xiaozhi-server`）：

```text
xiaozhi-server/
  ├─ docker-compose.local.yml
  ├─ .env                          # 数据库/端口/镜像名，勿提交
  ├─ data/.config.yaml             # server.secret 等，勿提交
  ├─ models/SenseVoiceSmall/model.pt
  └─ uploadfile/                   # 智控台上传文件
```

建议定期备份：

- `.env`、`data/.config.yaml`
- MySQL（`mysqldump xiaozhi_esp32_server`）
- `uploadfile/`
- `model.pt`（可选，丢了能再下载）

示例：

```bash
BACKUP=/opt/xiaozhi/backup/$(date +%Y%m%d)
mkdir -p "$BACKUP"
cp "$REPO/main/xiaozhi-server/.env" "$BACKUP/"
cp "$REPO/main/xiaozhi-server/data/.config.yaml" "$BACKUP/"
docker exec xiaozhi-esp32-server-db \
  mysqldump -uroot -p'你的密码' xiaozhi_esp32_server > "$BACKUP/xiaozhi_esp32_server.sql"
```

（若 MySQL 不在名为 `xiaozhi-esp32-server-db` 的容器里，改成实际连接方式。）

---

## 12. 命令速查

```bash
# 路径
export REPO=/opt/xiaozhi/xiaozhi-esp32-server
cd "$REPO/main/xiaozhi-server"

# 拉代码
cd "$REPO" && git pull origin main

# 检查环境 / 下模型 / 构建镜像
./check-install-local.sh --build          # 在 main/xiaozhi-server
# 或仓库根目录
./docker-build-local.sh

# 启动
docker compose -f docker-compose.local.yml up -d

# 重启
docker restart xiaozhi-esp32-server
docker restart xiaozhi-esp32-server-web
docker compose -f docker-compose.local.yml up -d   # 改过 .env 时用这个

# 停止
docker compose -f docker-compose.local.yml down

# 日志
docker logs -f -n 50 xiaozhi-esp32-server
docker logs -f -n 50 xiaozhi-esp32-server-web
```

---

## 13. 相关文档

- 全模块官方镜像部署（含自带 MySQL/Redis）：[Deployment_all.md](./Deployment_all.md)
- 仅 Server 官方镜像：[Deployment.md](./Deployment.md)
- 从源码改镜像标签：[docker-build.md](./docker-build.md)
- 源码非 Docker 自动编译脚本：[dev-ops-integration.md](./dev-ops-integration.md)
- 离线打包：仓库根目录 `./docker-package-local.sh`（生成 `dist/xiaozhi-deploy-*.tar.gz`，目标机解压后 `./download-model.sh && ./install.sh`）
