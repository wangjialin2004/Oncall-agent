"""Process-wide async Redis client for the harness checkpoint subsystem.

Mirrors the lazy-singleton pattern in :mod:`app.services.memory_cache`: the
underlying ``redis.asyncio.Redis`` instance is created on first use so test
fixtures that mutate ``config.redis_*`` between tests get a fresh client.

This module is intentionally narrow: it exposes only the small surface the
checkpoint store needs (``get`` / ``set`` / ``delete`` / ``scan`` plus a
``ping`` for health checks). Business code should depend on
:mod:`app.services.harness_checkpoint`, not on this client directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from loguru import logger

from app.config import config

try:  # redis is a hard dependency declared in pyproject.toml, but keep this
    # defensive so the rest of the app still imports in environments where the
    # optional dep has been pruned.
    from redis.asyncio import Redis as _AsyncRedis
except Exception:  # pragma: no cover - environment without redis installed
    _AsyncRedis = None  # type: ignore[assignment]


_DEFAULT_CLIENT: _AsyncRedis | None = None
_DEFAULT_CLIENT_LOCK = asyncio.Lock()
_REDIS_STATUS: dict[str, str] = {"status": "unknown"}


def redis_health_snapshot() -> dict[str, str]:
    """Return the last lifecycle ping state without performing network I/O."""

    return dict(_REDIS_STATUS)


def _set_redis_status(status: str, reason: str = "") -> None:
    global _REDIS_STATUS
    _REDIS_STATUS = {"status": status}
    if reason:
        _REDIS_STATUS["reason"] = reason


def is_redis_available() -> bool:
    """Return True when the redis package import succeeded."""
    return _AsyncRedis is not None


def build_redis_from_settings() -> Any:
    """Build a Redis client from current settings, without caching it."""
    if _AsyncRedis is None:  # pragma: no cover - guarded by is_redis_available
        raise RuntimeError("redis package is not installed")
    timeout = float(getattr(config, "redis_socket_timeout", 5.0) or 5.0)
    protocol = int(getattr(config, "redis_protocol", 2) or 2)
    return _AsyncRedis.from_url(
        str(getattr(config, "redis_url", "redis://localhost:6379/0")),
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
        decode_responses=True,
        protocol=protocol,
    )


async def get_redis_client() -> Any:
    """Return the process-wide async Redis client, creating it lazily.

    Settings are read lazily so tests that mutate ``config.redis_url`` between
    cases get a fresh client that reflects the new values.
    """
    global _DEFAULT_CLIENT
    if _AsyncRedis is None:  # pragma: no cover - environment without redis
        raise RuntimeError("redis package is not installed")
    if _DEFAULT_CLIENT is not None:
        return _DEFAULT_CLIENT
    async with _DEFAULT_CLIENT_LOCK:
        if _DEFAULT_CLIENT is None:
            _DEFAULT_CLIENT = build_redis_from_settings()
    return _DEFAULT_CLIENT


async def reset_redis_client() -> None:
    """Drop the cached client. Reserved for test fixtures and operator tooling."""
    global _DEFAULT_CLIENT
    async with _DEFAULT_CLIENT_LOCK:
        if _DEFAULT_CLIENT is not None:
            try:
                await _DEFAULT_CLIENT.aclose()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(f"redis 客户端关闭失败（已忽略）: {exc}")
            _DEFAULT_CLIENT = None


@asynccontextmanager
async def redis_lifespan() -> AsyncIterator[None]:
    """Long-running context manager for FastAPI startup/shutdown.

    Used by ``app.main`` to verify connectivity at startup and close cleanly on
    shutdown. Yields nothing; consumers should call :func:`get_redis_client`
    directly.
    """
    if not bool(getattr(config, "redis_enabled", False)):
        _set_redis_status("disabled")
        yield
        return
    if _AsyncRedis is None:  # pragma: no cover - dependency missing
        _set_redis_status("degraded", "redis_package_missing")
        logger.warning("redis_enabled=true 但 redis 包不可用，跳过客户端初始化")
        yield
        return
    try:
        client = await get_redis_client()
        await client.ping()
        _set_redis_status("ready")
        logger.info("redis 客户端已就绪")
    except Exception as exc:
        _set_redis_status("degraded", type(exc).__name__)
        logger.warning(f"redis ping 失败（不影响主链路）: {exc}")
    try:
        yield
    finally:
        await reset_redis_client()
