# DS-160 Docker 部署说明

## 端口规划

- 对公网只暴露 `18000/tcp`，由 Docker Nginx 监听。
- 应用容器内部使用 `3000/tcp` 运行 Next.js，`8000/tcp` 运行 FastAPI。
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
docker compose up -d --build
```

## 服务器更新

主线代码推送到 GitHub 后，服务器可以直接在部署目录拉取并重建：

```bash
cd /opt/ds160-agent2
git pull --ff-only origin main
docker compose up -d --build
```

更新后确认容器和健康检查：

```bash
docker compose ps
curl -k https://127.0.0.1:18000/healthz -H "Host: ds160.efastt.store"
```

## Agent Runtime Canary

默认保持旧主流程：

```env
AGENT_RUNTIME=legacy
AGENT_RUNTIME_FAIL_OPEN_TO_LEGACY=true
```

上线观察顺序：

```bash
# 1. 旁路观察，用户可见回复仍由 legacy 写入。
AGENT_RUNTIME=graph_shadow docker compose up -d --build

# 2. 小流量切 graph，未命中 session 仍走 legacy。
AGENT_RUNTIME=graph_canary AGENT_RUNTIME_CANARY_PERCENT=10 docker compose up -d --build
AGENT_RUNTIME=graph_canary AGENT_RUNTIME_CANARY_PERCENT=25 docker compose up -d --build
AGENT_RUNTIME=graph_canary AGENT_RUNTIME_CANARY_PERCENT=50 docker compose up -d --build
AGENT_RUNTIME=graph_canary AGENT_RUNTIME_CANARY_PERCENT=100 docker compose up -d --build
```

每一档切换前后先跑：

```bash
./scripts/agent-runtime-canary-smoke.sh
docker compose ps
curl -k https://127.0.0.1:18000/healthz -H "Host: ds160.efastt.store"
```

回滚命令：

```bash
AGENT_RUNTIME=legacy AGENT_RUNTIME_CANARY_PERCENT=0 docker compose up -d --build
```

任一档出现新增 500、重复模板、无法解释冲突、citation 缺失率异常、fallback 率异常，直接回滚到上一档或 `legacy`。

## 本机验证

```bash
docker compose ps
curl -k https://127.0.0.1:18000/healthz -H "Host: ds160.efastt.store"
docker compose logs --tail=100 nginx
docker compose logs --tail=100 ds160-agent2
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
