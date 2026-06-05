# DS-160 Docker 部署说明

## 端口规划

- 对公网只暴露 `18000/tcp`，由 Docker Nginx 监听。
- Compose 默认拆成 `ds160-api`、`ds160-web`、`ds160-worker`、`postgres` 和
  `nginx`。
- `ds160-web` 容器内部使用 `3000/tcp` 运行 Next.js。
- `ds160-api` 容器内部使用 `8000/tcp` 运行 FastAPI。
- `ds160-worker` 独立运行材料理解/解析 worker。
- 不使用 sing-box 已占用端口，也不使用 `37666-38666`。

## 服务器启动

```bash
cd /opt/ds160-agent2
mkdir -p deploy/certs
openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
  -keyout deploy/certs/origin.key \
  -out deploy/certs/origin.crt \
  -subj "/CN=ds160.efastt.store"
chmod 600 deploy/certs/origin.key

BUILD_SHA="$(git rev-parse --short HEAD)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
APP_GIT_SHA="$BUILD_SHA" \
NEXT_PUBLIC_GIT_SHA="$BUILD_SHA" \
APP_BUILD_TIME="$BUILD_TIME" \
NEXT_PUBLIC_BUILD_TIME="$BUILD_TIME" \
docker compose up -d --build postgres ds160-api ds160-web ds160-worker

docker compose up -d nginx
```

## 服务器更新

生产服务器资源较弱，不推荐在服务器上执行 `docker compose up --build`。
当前前端产物在镜像构建期生成，后端代码也打进镜像；因此 `git pull && docker compose up -d --no-build`
只会重启旧镜像，不能发布代码变更。

推荐的低负载发布路径是：在本地或 CI 构建镜像，传输到服务器 `docker load`，服务器只做
`docker compose up -d --no-build` 重建容器。

### 推荐：预构建镜像后在服务器无构建发布

本地或 CI：

```bash
cd ds160-visa-simulator
git fetch origin simplify/agent-runtime-core
git checkout simplify/agent-runtime-core
git pull --ff-only origin simplify/agent-runtime-core

BUILD_SHA="$(git rev-parse --short HEAD)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
IMAGE="ds160-agent2:${BUILD_SHA}"

docker build   --build-arg APP_GIT_SHA="$BUILD_SHA"   --build-arg APP_BUILD_TIME="$BUILD_TIME"   --build-arg NEXT_PUBLIC_GIT_SHA="$BUILD_SHA"   --build-arg NEXT_PUBLIC_BUILD_TIME="$BUILD_TIME"   -t "$IMAGE" .

docker save "$IMAGE" | gzip > "ds160-agent2-${BUILD_SHA}.tar.gz"
scp "ds160-agent2-${BUILD_SHA}.tar.gz" root@conectv6.302dog.icu:/opt/ds160-agent2/
```

服务器：

```bash
cd /opt/ds160-agent2
git fetch origin simplify/agent-runtime-core
git pull --ff-only origin simplify/agent-runtime-core

BUILD_SHA="$(git rev-parse --short HEAD)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
gunzip -c "ds160-agent2-${BUILD_SHA}.tar.gz" | docker load
RELEASE_IMAGE="ds160-agent2:${BUILD_SHA}" APP_GIT_SHA="$BUILD_SHA" APP_BUILD_TIME="$BUILD_TIME" scripts/production-release-preloaded-image.sh
```

这个路径不会在服务器上运行 Docker build / pnpm build / uv sync；服务器只解压镜像、更新 `.env`
中的发布 metadata、重启容器。

### 仅适合资源充足主机：服务器直接重建

如果明确接受服务器 CPU/内存压力，才使用远端重建：

```bash
cd /opt/ds160-agent2
git fetch origin simplify/agent-runtime-core
git pull --ff-only origin simplify/agent-runtime-core

BUILD_SHA="$(git rev-parse --short HEAD)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
APP_GIT_SHA="$BUILD_SHA" NEXT_PUBLIC_GIT_SHA="$BUILD_SHA" APP_BUILD_TIME="$BUILD_TIME" NEXT_PUBLIC_BUILD_TIME="$BUILD_TIME" docker compose up -d --build postgres ds160-api ds160-web ds160-worker nginx
```

更新后确认容器和健康检查：

```bash
docker compose ps
curl -k https://127.0.0.1:18000/healthz -H "Host: ds160.efastt.store"
curl -k https://127.0.0.1:18000/api/version -H "Host: ds160.efastt.store"
curl --noproxy '*' -fsS https://ds160.efastt.store/healthz
```

## SQLite 到 Postgres cutover 脚本

正式迁移可以使用仓库脚本，但它会停止旧 combined 容器并启动 split services，
只应在维护窗口内运行。脚本会先备份 `.env` 和 SQLite，再执行 migration dry-run；
只有 dry-run 之后才执行真实写入。

```bash
CONFIRM_PRODUCTION_CUTOVER=I_UNDERSTAND_PRODUCTION_CUTOVER \
RUN_WRITE_MIGRATION=1 \
scripts/production-split-postgres-cutover.sh
```

如果目标 Postgres 非空，只有在已备份并确认维护窗口后才允许加：

```bash
TRUNCATE_TARGET=1
```

## Agent Runtime

当前公开主流程默认由 native interviewer 接管；`graph` / `graph_canary` 是兼容标签，
`graph_shadow` 也是兼容标签，不再在公开请求中旁路运行第二套 graph。legacy 只作为显式回滚路径：

```env
AGENT_RUNTIME=native_interviewer
AGENT_RUNTIME_TYPED_ADJUDICATION_ENABLED=true
```

如需评估 LangGraph，只运行 replay/eval 或专门的 public promotion 分支；不要在生产公开请求里打开并发 shadow。

切换前后先跑：

```bash
docker compose ps
curl -k https://127.0.0.1:18000/healthz -H "Host: ds160.efastt.store"
curl -k https://127.0.0.1:18000/api/version -H "Host: ds160.efastt.store"
```

回滚命令：

```bash
AGENT_RUNTIME=legacy AGENT_RUNTIME_CANARY_PERCENT=0 docker compose up -d ds160-api ds160-worker
```

出现新增 500、重复模板、无法解释冲突、citation 缺失率异常、fallback 率异常时，直接回滚到 `native_interviewer` 或显式 `legacy`。

## 本机验证

```bash
docker compose ps
curl -k https://127.0.0.1:18000/healthz -H "Host: ds160.efastt.store"
docker compose logs --tail=100 nginx
docker compose logs --tail=100 ds160-api ds160-worker ds160-web
```

## 流式接口

`/api/v1/.../stream` 使用 SSE。Nginx 配置需要对 `/api/` 关闭 `proxy_buffering` 和 `proxy_cache`，否则浏览器可能只能在后端全部完成后一次性收到事件，看起来像“卡住”。当前 `deploy/nginx/ds160.conf` 已包含该配置。

## Cloudflare 配置

DNS 保持橙云代理：

```text
A  ds160  <origin-server-ip>
```

创建 Origin Rule：

```text
Hostname equals ds160.efastt.store
Destination Port = 18000
```

SSL/TLS 模式建议先使用 `Full`。当前部署生成的是源站自签名证书；如果改用 `Full (strict)`，需要在 Cloudflare 创建 Origin Certificate 并替换 `deploy/certs/origin.crt` 与 `deploy/certs/origin.key`。
