"""Redis health inspection tool."""

from __future__ import annotations

import json
from time import perf_counter
from urllib.parse import urlparse

from app.config import config
from app.core.runtime_tools import make_runtime_tool
from app.services.redis_client import build_redis_from_settings, is_redis_available


def _redis_target_label() -> str:
    parsed = urlparse(str(getattr(config, "redis_url", "redis://localhost:6379/0")))
    scheme = parsed.scheme or "redis"
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    db = (parsed.path or "/0").lstrip("/") or "0"
    return f"{scheme}://{host}:{port}/{db}"


async def _check_redis_health() -> str:
    """Directly ping the configured Redis checkpoint backend.

    Use this when the user asks whether Redis is connected, reachable, healthy,
    writable enough for checkpointing, or whether Redis-backed checkpoint keys
    can be inspected. This is read-only except for Redis PING and does not
    expose credentials.

    Returns:
        str: JSON with success, target, namespace, protocol, latency_ms, and message.
    """

    if not bool(getattr(config, "redis_enabled", False)):
        return json.dumps(
            {
                "success": False,
                "status": "disabled",
                "target": _redis_target_label(),
                "namespace": str(getattr(config, "redis_namespace", "")),
                "message": "Redis is disabled by redis_enabled=false.",
            },
            ensure_ascii=False,
        )

    if not is_redis_available():
        return json.dumps(
            {
                "success": False,
                "status": "dependency_missing",
                "target": _redis_target_label(),
                "namespace": str(getattr(config, "redis_namespace", "")),
                "message": "redis Python package is not available.",
            },
            ensure_ascii=False,
        )

    client = None
    started = perf_counter()
    try:
        client = build_redis_from_settings()
        pong = await client.ping()
        latency_ms = round((perf_counter() - started) * 1000, 2)
        return json.dumps(
            {
                "success": bool(pong),
                "status": "connected" if pong else "unexpected_pong",
                "target": _redis_target_label(),
                "namespace": str(getattr(config, "redis_namespace", "")),
                "protocol": int(getattr(config, "redis_protocol", 2) or 2),
                "latency_ms": latency_ms,
                "message": "Redis ping succeeded." if pong else "Redis ping returned a non-true response.",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        latency_ms = round((perf_counter() - started) * 1000, 2)
        return json.dumps(
            {
                "success": False,
                "status": "unreachable",
                "target": _redis_target_label(),
                "namespace": str(getattr(config, "redis_namespace", "")),
                "protocol": int(getattr(config, "redis_protocol", 2) or 2),
                "latency_ms": latency_ms,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "message": "Redis ping failed.",
            },
            ensure_ascii=False,
        )
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


check_redis_health = make_runtime_tool(
    name="check_redis_health",
    description=_check_redis_health.__doc__ or "",
    func=_check_redis_health,
)
