# Cost / Quality Dashboard (PromQL snippets)

> **Date**: 2026-07-15 · **M3 W11**  
> **Scope**: documentation panel — no Grafana cluster required.  
> **Scrape**: backend `GET /metrics` (default port `9900`).

## Scrape checklist

```bash
# Local pilot
curl -sS http://127.0.0.1:9900/metrics | findstr agent_

# Docker / compose: ensure prometheus.yml targets the backend
# deploy/prometheus/prometheus.yml → job scraping app:9900/metrics
```

Prometheus job example (if self-hosting):

```yaml
- job_name: oncall-agent
  metrics_path: /metrics
  static_configs:
    - targets: ["host.docker.internal:9900"]
```

## Metric catalog (Agent)

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `agent_runs_total` | Counter | `status` | Terminal runs (`completed`/`degraded`/`timeout`/…) |
| `agent_latency_seconds` | Histogram | — | End-to-end harness latency |
| `agent_re_evidence_total` | Counter | — | Re-evidence rounds |
| `agent_replan_total` | Counter | — | Replan events |
| `agent_delegate_parallel_total` | Counter | — | Parallel fan-outs |
| **`agent_tokens_total`** | Counter | `role`∈prompt/completion/total | **W11** LLM tokens from `usage_total` |
| **`agent_tool_calls_total`** | Counter | `tool`, `status`∈ok/error/timeout/other | **W11** tool calls (tool name whitelist) |

`tool` is cardinality-capped: unknown names collapse to `other` (and a few aliases map into known buckets).

## Copy-paste PromQL

### Traffic / success

```promql
# Runs per second by status
sum by (status) (rate(agent_runs_total[5m]))

# Completions only
sum(rate(agent_runs_total{status="completed"}[5m]))
```

### Latency

```promql
# P50
histogram_quantile(0.5, sum(rate(agent_latency_seconds_bucket[15m])) by (le))

# P95
histogram_quantile(0.95, sum(rate(agent_latency_seconds_bucket[15m])) by (le))
```

### Cost / tokens (W11)

```promql
# Token rate by role
sum by (role) (rate(agent_tokens_total[1h]))

# Completion tokens only (often the paid dimension)
sum(rate(agent_tokens_total{role="completion"}[1h]))

# Rough 1h total tokens (increase)
sum(increase(agent_tokens_total{role="total"}[1h]))
```

Cost dollars depend on your provider price card — multiply `completion`/`prompt` increases offline; this panel does **not** invent unit prices.

### Tools

```promql
# Tool call rate by tool + status
sum by (tool, status) (rate(agent_tool_calls_total[15m]))

# Error share
sum(rate(agent_tool_calls_total{status="error"}[15m]))
  /
sum(rate(agent_tool_calls_total[15m]))
```

### Quality loop

```promql
sum(rate(agent_re_evidence_total[1h]))
sum(rate(agent_replan_total[1h]))
sum(rate(agent_delegate_parallel_total[1h]))
```

## Operator notes

1. Metrics are **best-effort** on the complete path — missing `usage_total` simply skips token counters.
2. Trace export is **independent** (`HARNESS_TRACE_EXPORT_ENABLED` + `HARNESS_TRACE_SAMPLE_RATE`); metrics stay on even when traces are off.
3. OTEL is optional (`OTEL_EXPORTER_OTLP_ENDPOINT` empty = no-op). See [otel-optional.md](./otel-optional.md).
4. Online human scoring uses [online-eval-template.md](./online-eval-template.md) + `scripts/sample_online_runs.py`.

## Rollback

Counters cannot be “turned off” without code rollback, but they are write-only and safe. To stop trace/OTEL side effects:

```text
HARNESS_TRACE_EXPORT_ENABLED=false
HARNESS_TRACE_SAMPLE_RATE=0
OTEL_EXPORTER_OTLP_ENDPOINT=
```
