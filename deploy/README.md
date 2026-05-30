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

主线代码推送到 GitHub 后，服务器可以直接在部署目录拉取并重建：

```bash
cd /opt/ds160-agent2
git fetch origin refactor/agent-runtime-graph
git pull --ff-only origin refactor/agent-runtime-graph

BUILD_SHA="$(git rev-parse --short HEAD)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
APP_GIT_SHA="$BUILD_SHA" \
NEXT_PUBLIC_GIT_SHA="$BUILD_SHA" \
APP_BUILD_TIME="$BUILD_TIME" \
NEXT_PUBLIC_BUILD_TIME="$BUILD_TIME" \
docker compose up -d --build postgres ds160-api ds160-web ds160-worker nginx
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
`graph_shadow` 只做 shadow/eval trace。legacy 只作为显式回滚路径：

```env
AGENT_RUNTIME=native_interviewer
AGENT_RUNTIME_FAIL_OPEN_TO_LEGACY=false
AGENT_RUNTIME_TYPED_ADJUDICATION_ENABLED=true
```

如需观察 graph shadow，只运行 shadow，不把用户可见回复交给 graph：

```bash
AGENT_RUNTIME=graph_shadow docker compose up -d ds160-api ds160-worker
```

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
