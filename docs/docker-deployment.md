# Docker 部署

根目录的 `compose.yaml` 提供一套可复用的 Docker 栈：FastAPI 后端、React/Nginx 前端、Redis、Milvus、etcd、MinIO，以及可选的 Attu 和 Prometheus。

## 首次启动

```bash
cp .env.example .env
# 编辑 .env，至少填写 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL、AUTH_TOKEN_SECRET
docker compose --profile monitoring --profile tools up -d --build
```

访问地址：

- 前端：<http://localhost:5173>
- 后端 API：<http://localhost:9900>
- API 文档：<http://localhost:9900/docs>
- Prometheus：<http://localhost:9091>（可用 `PROMETHEUS_HOST_PORT` 修改）
- Attu：<http://localhost:8001>（可用 `ATTU_HOST_PORT` 修改）

如果不需要 Attu 或 Prometheus，可以省略对应 profile：

```bash
docker compose up -d --build
```

## 网络和数据

容器内部使用 Compose 服务名连接依赖：Redis 是 `redis:6379`，Milvus 是 `milvus:19530`，Prometheus 是 `prometheus:9090`。不要在容器内把这些地址写成 `localhost`。

后端使用项目目录下的 `./volumes` bind mount，因此已有的 `volumes/long_term_memory.db`、checkpoint 和本地文件会被容器直接复用。Redis、etcd、MinIO、Milvus 和 Prometheus 使用 Docker volumes。不要在不需要清空数据时使用 `docker compose down -v`。

首次部署或升级 schema 前，先查看并显式执行迁移：

```bash
docker compose exec backend python scripts/migrate_database.py status
docker compose exec backend python scripts/migrate_database.py up
```

## 可选能力

这套 Compose 默认将 `HARNESS_MCP_ENABLED` 设为 `false`，因为 CLS/Monitor MCP 服务没有包含在栈内。只有在另行部署 MCP 服务并配置 `MCP_CLS_URL`、`MCP_MONITOR_URL` 后，才应开启 MCP。

Dockerfile 默认不安装体积较大的本地 BGE-M3 embedding extra。需要本地 embedding 时，应基于 `deploy/compose/Dockerfile.backend` 制作带 `.[embedding]` 的专用镜像；否则配置一个可用的 DashScope embedding provider。

## 停止和排障

```bash
docker compose ps
docker compose logs -f backend
docker compose down
```

若宿主机端口被占用，可在 `.env` 或 shell 中设置 `ATTU_HOST_PORT`、`PROMETHEUS_HOST_PORT`，例如：

```bash
ATTU_HOST_PORT=18000 PROMETHEUS_HOST_PORT=19090 docker compose --profile monitoring --profile tools up -d
```
