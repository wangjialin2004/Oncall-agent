# Docker 栈进度（2026-08-06）

## 变更

- 新增根目录 `compose.yaml`，统一编排 backend、frontend、Redis、Milvus、etcd、MinIO，以及可选 Attu/Prometheus。
- 新增多阶段前端镜像和 Nginx SSE/API 反向代理。
- 新增 Docker 专用 Prometheus 配置，抓取 Compose 内部的 `backend:9900`。
- 新增 `.dockerignore` 和部署说明。
- Docker 栈默认关闭 MCP，因为本次 Compose 未包含 CLS/Monitor MCP 服务；SQLite 使用现有 `./volumes`，Redis/Milvus 等依赖使用命名 volume 持久化。
- Backend now bind-mounts `./volumes` so existing SQLite conversations are reused; the first named `backend-data` volume remains untouched but is no longer used.

## 验证证据

| 检查 | 结果 |
|---|---|
| `docker compose config --quiet` | 通过 |
| `npm --prefix frontend run build` | 通过；仅有既有 dynamic import warning |
| `docker compose --profile monitoring --profile tools build --progress=plain` | backend/frontend 镜像构建通过 |
| backend readiness | `200 ready` |
| frontend `GET /` | `200`，Nginx 正常提供构建产物 |
| Prometheus `/-/ready` | `200` |
| Prometheus targets | API 与 Prometheus 均 `up` |
| Attu | `200`，默认映射到宿主机 `8001` |
| Existing conversation data after bind mount | `73` conversations / `116` turns visible inside backend |

首次启动时宿主机 `8000`、`9090` 已占用，故将 Attu 和 Prometheus 外部默认端口设为 `8001`、`9091`，均可通过环境变量覆盖。

## 未覆盖范围

- 未将 CLS/Monitor MCP 服务加入 Compose；启用 MCP 前需另行容器化并配置 URL。
- 未在镜像中安装 BGE-M3 embedding extra；本地 embedding 需要专用镜像或改用远端 embedding provider。
- 未执行真实 LLM 对话 E2E；需要有效的 `.env` 上游模型凭证后再验证。
