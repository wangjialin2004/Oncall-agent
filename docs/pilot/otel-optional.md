# OTEL Optional Export (W11 ADR)

> **Date**: 2026-07-15 · **M3 W11 / WP-G3**  
> **Status**: minimal wiring + ADR — default **off**

## Decision

| Option | Choice | Why |
|---|---|---|
| Default export | **Off** | Avoid disk/privacy/dependency cost on pilot |
| Config | `OTEL_EXPORTER_OTLP_ENDPOINT` / `otel_exporter_otlp_endpoint` empty = no-op | Zero import of opentelemetry when unset |
| When set | Best-effort root span attributes on complete | Never fails SSE |
| Package | Optional `opentelemetry` SDK | Missing package → debug log + skip |
| Full OTLP HTTP/gRPC | **Deferred** (W12+) | W11 avoids dependency hell; console exporter only if SDK present |

## Alternatives considered

1. **JSON trace only** (`volumes/traces`) — already present; remains the default offline path.
2. **Langfuse / proprietary** — out of scope; can sit behind same endpoint decision later.
3. **Always-on OTEL** — rejected for P50 and pilot ops simplicity.

## Wire points

| Piece | Path |
|---|---|
| Config | `app/config.py` → `otel_exporter_otlp_endpoint` |
| Exporter | `app/agent/harness/otel_export.py` → `maybe_export_otel_span` |
| Call site | `loop.py` complete path (after metrics) |
| Env example | `.env.example` |

## Enable (dev only)

```text
# Requires: pip install opentelemetry-api opentelemetry-sdk
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
```

Empty endpoint → **zero behavior** (no import side effects beyond module load of our thin wrapper).

## Span attributes (skeleton)

- `session_id`, `route`, `status`, `latency_seconds`
- `re_evidence_rounds`, `replan_times`
- `usage.prompt_tokens` / `completion_tokens` / `total_tokens` when present
- `otel.endpoint` (truncated, non-secret)

## Security

- No prompts, tool raw results, or API keys in attributes
- Failures logged at warning; complete path continues

## Follow-ups (W12+)

- Real `OTLPSpanExporter` (HTTP/gRPC) behind optional extra
- Sampling independent of JSON `HARNESS_TRACE_SAMPLE_RATE`
- Correlate with Prometheus `agent_*` via `trace_id` if product needs it
