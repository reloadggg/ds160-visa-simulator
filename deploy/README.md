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

## 本机验证

```bash
docker compose ps
curl -k https://127.0.0.1:18000/healthz -H "Host: ds160.efastt.store"
docker compose logs --tail=100 nginx
docker compose logs --tail=100 ds160-agent2
```

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
