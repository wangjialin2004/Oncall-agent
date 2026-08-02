# Prometheus Compose Project Membership Implementation Plan

**Goal:** 把现有 `aiops-prometheus` 纳入项目主 Compose 配置，消除 `make up` 的 orphan 警告，同时保持 Prometheus 为显式启用的可选监控组件。

**Architecture:** `vector-database.yml` 继续作为项目唯一的本地基础 Compose 文件，并定义带 `monitoring` profile 的 Prometheus 服务。`make up` 不启用 profile，保留既有的最小 Milvus 启动默认值；`make start-prometheus` 显式启用 profile。Prometheus 继续通过 `host.docker.internal:host-gateway` 抓取宿主 FastAPI 的 `:9900/metrics`，且复用命名卷 `prometheus-data`。

**Tech Stack:** Docker Compose v2, Prometheus `quay.io/prometheus/prometheus:v2.54.1`, GNU Make.

---

## Problem

`aiops-prometheus` 由独立的 `monitoring.yml` 创建，而 `make up` 只读取 `vector-database.yml`。二者使用相同的 Compose project name，因此启动基础栈时 Docker Compose 将正在运行的 Prometheus 标为 orphan。配置来源重复也让 `make start-prometheus` 与完整启动脚本无法作为同一个项目图维护。

## Decisions and defaults

- 采用 `vector-database.yml` 的 `monitoring` profile 承载 Prometheus；不把它加入 `make up` 默认启动集合。
- `make start-prometheus` 使用 `--profile monitoring up -d prometheus`；`make stop-prometheus` 只停止 `prometheus`，不执行整个项目的 `down`。
- 删除重复的根 `monitoring.yml`，防止两个 Compose 定义漂移。历史 handoff 文档不回写；当前 README 和 Makefile 改为主 Compose 命令。
- 固定现有 `quay.io/prometheus/prometheus:v2.54.1`，不在本次配置收敛中升级至上游当前的 v3 系列。

## Scope and non-goals

In scope: 主 Compose 服务、Make 目标、当前启动文档、旧容器的受控重建和 Prometheus/API 完整抓取验证。

Out of scope: Prometheus 规则修改、Grafana、远端监控部署、`MONITOR_TARGET_MODE` 默认值、Prometheus 主版本升级和删除指标数据卷。

## Affected files

- Modify: `vector-database.yml` - 增加 profile 化的 `prometheus` 服务和 `prometheus-data` 命名卷。
- Modify: `Makefile` - 让 Prometheus 目标只操作主 Compose 文件中的服务。
- Modify: `README.md` - 将本地 Prometheus 命令更新为 profile 化主 Compose 命令。
- Delete: `monitoring.yml` - 删除重复的独立 Compose 定义。
- Modify: `AGENTS.md` - 登记计划与验证状态。
- Modify: `plan/2026-08-02-compose-prometheus-project-membership.md` - 记录实际验证证据和偏差。

## Dependency and security review

- Docker Compose profiles: [official documentation](https://docs.docker.com/compose/how-tos/profiles/) and [multiple-file guidance](https://docs.docker.com/compose/how-tos/multiple-compose-files/) were reachable on 2026-08-02. Profiles support an explicit optional service without a second project configuration.
- Prometheus: [GitHub release v2.54.1](https://github.com/prometheus/prometheus/releases/tag/v2.54.1), published 2024-08-27, is the installed image; upstream [latest release v3.13.2](https://github.com/prometheus/prometheus/releases/tag/v3.13.2) was published 2026-07-30, confirming active maintenance. The pinned source [license](https://raw.githubusercontent.com/prometheus/prometheus/v2.54.1/LICENSE) is Apache-2.0.
- Adoption rationale: no new dependency is introduced. Keeping the existing tested v2 image avoids an unrequested major-version behavior change. The service runs as `nobody`, mounts configuration read-only, and preserves the existing local-only risk profile of port 9090.

## Flags and rollback

- Degradation switch: the `monitoring` Compose profile is disabled by default. Operators can omit `--profile monitoring` or run `make stop-prometheus` to disable Prometheus while leaving Milvus and the API intact.
- Rollback: restore `monitoring.yml`, remove the profile service and volume from `vector-database.yml`, and restore the two Make commands. The `prometheus-data` named volume is retained throughout and is never removed by this work.

## Tasks

### Task 1: Consolidate the Compose service

- [x] Add this exact service and volume to `vector-database.yml`:

```yaml
  prometheus:
    profiles: ["monitoring"]
    image: quay.io/prometheus/prometheus:v2.54.1
    container_name: aiops-prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./deploy/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./deploy/prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro
      - prometheus-data:/prometheus
    extra_hosts:
      - "host.docker.internal:host-gateway"
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
    restart: unless-stopped

volumes:
  prometheus-data:
```

- [x] Run `docker compose -f vector-database.yml --profile monitoring config --quiet`.
- [x] Delete `monitoring.yml` after the merged config validates, leaving no duplicate service definition.

### Task 2: Align Make and current operator documentation

- [x] Replace the Make start command with:

```make
	@docker compose -f vector-database.yml --profile monitoring up -d prometheus
```

- [x] Replace the Make stop command with:

```make
	@docker compose -f vector-database.yml --profile monitoring stop prometheus
```

- [x] Replace current README references to `monitoring.yml` with `docker compose -f vector-database.yml --profile monitoring up -d prometheus` and keep `make start-prometheus` as the short form.
- [x] Run `make -n start-prometheus stop-prometheus` and confirm neither command invokes `monitoring.yml` or a whole-project `down`.

### Task 3: Adopt the running container and verify the complete path

- [x] Recreate only Prometheus into the consolidated service with:

```bash
docker compose -f vector-database.yml --profile monitoring up -d --force-recreate prometheus
```

- [x] Verify project membership and service health:

```bash
docker compose -f vector-database.yml --profile monitoring ps prometheus
curl --noproxy '*' -fsS http://127.0.0.1:9090/-/ready
curl --noproxy '*' -fsS http://127.0.0.1:9090/api/v1/targets
```

- [x] Run `make up`; expected result: successful base-stack start with no `Found orphan containers ([aiops-prometheus])` warning.
- [x] Verify the running FastAPI `GET /health/readiness` returns 200 and Prometheus target `aiops-assistant-api` is present. If the API target is down, record it as a scrape-path degradation instead of changing monitoring configuration.

## Exit criteria

- `aiops-prometheus` is owned by `vector-database.yml` and has the main Compose project labels.
- `make up` emits no orphan warning for `aiops-prometheus`.
- Prometheus can be explicitly started/stopped without stopping Milvus or deleting `prometheus-data`.
- Compose configuration, FastAPI readiness, Prometheus readiness, and target discovery all pass or any upstream scrape degradation is recorded.

## Progress

- 2026-08-02: Consolidated the service into `vector-database.yml` under the disabled-by-default `monitoring` profile; updated Make targets and README; removed the duplicate `monitoring.yml`.
- 2026-08-02: `docker compose -f vector-database.yml config --quiet` and `docker compose -f vector-database.yml --profile monitoring config --quiet` both exited 0. `make -n start-prometheus stop-prometheus` emits only the profile-based per-service commands. Scoped `git diff --check` exited 0.
- 2026-08-02: Recreated `aiops-prometheus` as `prometheus` from `/home/wangjialin/projects/super_biz_agent_py/vector-database.yml`; its existing `super_biz_agent_py_prometheus-data` volume was retained. `http://127.0.0.1:9090/-/ready` returned `Prometheus Server is Ready.` and `make up` completed without an `aiops-prometheus` orphan warning.
- 2026-08-02: Automated runtime path: FastAPI `GET /health/readiness` returned 200; Prometheus discovered both `aiops-assistant-api` and its own target. The self target is `up`, while `aiops-assistant-api` is `down` with `dial tcp 172.17.0.1:9900: connect: connection refused`. This existing host-to-container reachability issue is intentionally not changed in this configuration-only scope.
- 2026-08-02: `tests/test_prometheus_integration.py -q` has a pre-existing incompatible baseline: 7 failed, 6 passed. Failures show `metrics access denied` and direct calls to `FunctionTool`; the test and its referenced runtime files are outside this change and were left intact.
