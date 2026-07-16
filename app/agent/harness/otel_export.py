"""Optional OTEL export skeleton (M3 W11).

Default: no-op when ``OTEL_EXPORTER_OTLP_ENDPOINT`` / config endpoint is empty.
Never imports opentelemetry unless endpoint is configured **and** the package is
installed. Failures must not break the harness complete path.

See docs/pilot/otel-optional.md for the selection ADR.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.config import config


def _endpoint() -> str:
    return str(getattr(config, "otel_exporter_otlp_endpoint", "") or "").strip()


def maybe_export_otel_span(
    *,
    session_id: str,
    route: str | None = None,
    status: str | None = None,
    latency_seconds: float | None = None,
    re_evidence_rounds: int = 0,
    replan_times: int = 0,
    usage: dict[str, Any] | None = None,
    attributes: dict[str, Any] | None = None,
) -> bool:
    """Best-effort root span export. Returns True if something was attempted+ok.

    W11 ships the **wiring only**. Full OTLP exporter wiring can deepen in W12
    without changing the call site.
    """
    endpoint = _endpoint()
    if not endpoint:
        return False
    try:
        # Optional dependency — absent package → log once-level debug and skip.
        try:
            from opentelemetry import trace  # type: ignore
            from opentelemetry.sdk.trace import TracerProvider  # type: ignore
            from opentelemetry.sdk.trace.export import (  # type: ignore
                BatchSpanProcessor,
                ConsoleSpanExporter,
            )
        except ImportError:
            logger.debug(
                "OTEL endpoint set ({}) but opentelemetry not installed; skip span",
                endpoint,
            )
            return False

        # Minimal provider: attach console exporter when no real OTLP client.
        # Real OTLP HTTP/gRPC exporters remain optional; avoid hard dep in W11.
        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        tracer = provider.get_tracer("oncall.harness", "w11")
        with tracer.start_as_current_span("harness.run") as span:
            span.set_attribute("session_id", str(session_id or ""))
            if route:
                span.set_attribute("route", str(route))
            if status:
                span.set_attribute("status", str(status))
            if latency_seconds is not None:
                span.set_attribute("latency_seconds", float(latency_seconds))
            span.set_attribute("re_evidence_rounds", int(re_evidence_rounds or 0))
            span.set_attribute("replan_times", int(replan_times or 0))
            if isinstance(usage, dict):
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    val = usage.get(key)
                    if isinstance(val, (int, float)):
                        span.set_attribute(f"usage.{key}", int(val))
            if attributes:
                for key, val in list(attributes.items())[:20]:
                    if isinstance(val, (str, int, float, bool)):
                        span.set_attribute(f"extra.{key}", val)
            # Surface configured endpoint for operators (not a secret).
            span.set_attribute("otel.endpoint", endpoint[:200])
        try:
            provider.shutdown()
        except Exception:
            pass
        return True
    except Exception as exc:  # pragma: no cover - never break complete
        logger.warning("OTEL export failed: {}", exc)
        return False
