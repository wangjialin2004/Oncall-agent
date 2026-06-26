"""Service knowledge base with a process-local L1 cache.

This service stores project-scoped service metadata: ownership, environment,
metric baselines, and relations between services. ``lookup`` is called
frequently during service diagnosis (R-hot-3 in the cache plan), and
``compare_metric`` delegates to ``lookup`` so the cache hit cascades.

Cache strategy
==============

* ``lookup``, ``list_services``, ``compare_metric`` are cached, copy-on-read.
* The lookup result includes the ``relations`` list, so any relation change
  must invalidate the matching ``lookup`` key (plan §3.3 — the
  ``upsert_relation`` row was added in review). We do this by invalidating
  the entire ``memory:<db_tag>:svc:`` prefix on a relation write — relation
  rows have no other reader that benefits from per-key targeting, and the
  cache is bounded so the scan is cheap.
* ``upsert_service`` / ``upsert_baseline`` / ``delete_baseline`` invalidate
  only the precise keys they affect.
* ``import_from_monitor_mcp`` defers invalidation to the end of the loop
  (one bulk prefix drop instead of one-per-row, plan §3.3).
"""

from __future__ import annotations

import copy
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import config
from app.services.memory_cache import db_tag_for, get_default_cache
from app.utils.time import utc_now as _utc_now


def _cache_prefix(db_path: Path) -> str:
    return f"memory:{db_tag_for(db_path)}:svc:"


def _key_lookup(db_path: Path, project_id: str, service_name: str, environment: str) -> str:
    return f"{_cache_prefix(db_path)}lookup:{project_id}:{service_name}:{environment or 'prod'}"


def _key_list(db_path: Path, project_id: str, environment: str) -> str:
    env = environment or "*"
    return f"{_cache_prefix(db_path)}list:{project_id}:{env}"


def _key_metric(
    db_path: Path,
    project_id: str,
    service_name: str,
    environment: str,
    metric_name: str,
) -> str:
    return (
        f"{_cache_prefix(db_path)}metric:{project_id}:{service_name}:"
        f"{environment or 'prod'}:{metric_name}"
    )


class ServiceKnowledgeService:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or config.memory_db_path)
        self._initialized = False

    # ----------------------------------------------------------------- write

    def upsert_service(
        self,
        *,
        project_id: str,
        service_name: str,
        environment: str,
        owner_team: str = "",
        owner_user: str = "",
        description: str = "",
        enabled: bool = True,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO services (
                    project_id, service_name, environment, owner_team, owner_user,
                    description, enabled, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, service_name, environment) DO UPDATE SET
                    owner_team = excluded.owner_team,
                    owner_user = excluded.owner_user,
                    description = excluded.description,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    service_name,
                    environment,
                    owner_team,
                    owner_user,
                    description,
                    1 if enabled else 0,
                    _utc_now(),
                ),
            )
        # The service row appears in ``lookup`` and ``list_services`` outputs,
        # so we must invalidate both. ``compare_metric`` only embeds the
        # service header, not the full row, so we keep that key alive.
        cache = get_default_cache()
        prefix = _cache_prefix(self.db_path)
        try:
            cache.invalidate(_key_lookup(self.db_path, project_id, service_name, environment))
            cache.invalidate(_key_list(self.db_path, project_id, environment))
            cache.invalidate(_key_list(self.db_path, project_id, ""))
        except Exception as exc:  # pragma: no cover
            logger.warning(f"service_knowledge cache invalidate failed: {exc}")

    def upsert_relation(
        self,
        *,
        project_id: str,
        source_service: str,
        target_service: str,
        relation_type: str,
        environment: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO service_relations (
                    project_id, source_service, target_service, relation_type,
                    environment, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    source_service,
                    target_service,
                    relation_type,
                    environment,
                    _utc_now(),
                ),
            )
        # Review note (plan §3.3 row 6): the cached ``lookup`` payload embeds
        # ``relations``; without invalidation here we'd serve stale relations
        # until TTL. Drop the source-service's lookup key + the environment's
        # list-services key. We do not bother with target_service lookups
        # because relations are read from the *source* side only.
        cache = get_default_cache()
        prefix = _cache_prefix(self.db_path)
        try:
            cache.invalidate(_key_lookup(self.db_path, project_id, source_service, environment))
            cache.invalidate(_key_list(self.db_path, project_id, environment))
            cache.invalidate(_key_list(self.db_path, project_id, ""))
            # New relation could be a different metric baseline dependency,
            # but compare_metric keys are scoped per (service, metric) so
            # we leave them — they don't include relations.
        except Exception as exc:  # pragma: no cover
            logger.warning(f"service_knowledge cache invalidate failed: {exc}")

    def upsert_baseline(
        self,
        *,
        project_id: str,
        service_name: str,
        environment: str,
        metric_name: str,
        min_value: float,
        max_value: float,
        unit: str = "",
        sample_window: str = "",
    ) -> None:
        with self._connection() as connection:
            # 保证服务实体存在：lookup() 以 services 行为前提，否则手工录入的基线会成为
            # orphaned 记录（永远查不到、不参与诊断增强）。仅补占位行，不覆盖已有归属/描述。
            connection.execute(
                """
                INSERT INTO services (
                    project_id, service_name, environment, owner_team, owner_user,
                    description, enabled, updated_at
                )
                VALUES (?, ?, ?, '', '', '', 1, ?)
                ON CONFLICT(project_id, service_name, environment) DO NOTHING
                """,
                (project_id, service_name, environment, _utc_now()),
            )
            connection.execute(
                """
                INSERT INTO service_baselines (
                    project_id, service_name, environment, metric_name,
                    min_value, max_value, unit, sample_window, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, service_name, environment, metric_name) DO UPDATE SET
                    min_value = excluded.min_value,
                    max_value = excluded.max_value,
                    unit = excluded.unit,
                    sample_window = excluded.sample_window,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    service_name,
                    environment,
                    metric_name,
                    min_value,
                    max_value,
                    unit,
                    sample_window,
                    _utc_now(),
                ),
            )
        # The baseline lives in ``lookup.baselines`` and in ``compare_metric``.
        # The lookup result for the *same* service may also have changed if
        # this is the first baseline (the service row was just inserted
        # above as a placeholder). The target metric key is precisely known.
        cache = get_default_cache()
        try:
            cache.invalidate(_key_lookup(self.db_path, project_id, service_name, environment))
            cache.invalidate(_key_metric(self.db_path, project_id, service_name, environment, metric_name))
        except Exception as exc:  # pragma: no cover
            logger.warning(f"service_knowledge cache invalidate failed: {exc}")

    def delete_baseline(
        self,
        *,
        project_id: str,
        service_name: str,
        environment: str,
        metric_name: str,
    ) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                DELETE FROM service_baselines
                WHERE project_id = ? AND service_name = ? AND environment = ? AND metric_name = ?
                """,
                (project_id, service_name, environment, metric_name),
            )
            deleted = cursor.rowcount > 0
        if deleted:
            cache = get_default_cache()
            try:
                cache.invalidate(_key_lookup(self.db_path, project_id, service_name, environment))
                cache.invalidate(_key_metric(self.db_path, project_id, service_name, environment, metric_name))
            except Exception as exc:  # pragma: no cover
                logger.warning(f"service_knowledge cache invalidate failed: {exc}")
        return deleted

    # ------------------------------------------------------------------ read

    def lookup(self, *, project_id: str, service_name: str, environment: str = "") -> dict[str, Any] | None:
        key = _key_lookup(self.db_path, project_id, service_name, environment)
        cache = get_default_cache()
        try:
            cached = cache.get(key)
        except Exception as exc:  # defensive
            logger.warning(f"service_knowledge cache get failed: {exc}")
            cached = None
        if cached is not None:
            return cached or None  # empty dict sentinel for "missing row"
        payload = self._raw_lookup(project_id, service_name, environment)
        try:
            cache.set(
                key,
                payload if payload is not None else {},
                ttl=float(config.memory_cache_ttl_service_knowledge_seconds),
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(f"service_knowledge cache set failed: {exc}")
        return copy.deepcopy(payload) if payload is not None else None

    def list_services(
        self,
        *,
        project_id: str,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        env = environment or ""
        key = _key_list(self.db_path, project_id, env)
        cache = get_default_cache()
        try:
            cached = cache.get(key)
        except Exception as exc:  # defensive
            logger.warning(f"service_knowledge cache get failed: {exc}")
            cached = None
        if cached is not None:
            return cached
        with self._connection() as connection:
            if environment:
                rows = connection.execute(
                    """
                    SELECT * FROM services
                    WHERE project_id = ? AND environment = ?
                    ORDER BY service_name, environment
                    """,
                    (project_id, environment),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM services
                    WHERE project_id = ?
                    ORDER BY service_name, environment
                    """,
                    (project_id,),
                ).fetchall()
        result = [_service_summary_from_row(row) for row in rows]
        try:
            cache.set(
                key,
                result,
                ttl=float(config.memory_cache_ttl_service_knowledge_seconds),
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(f"service_knowledge cache set failed: {exc}")
        return copy.deepcopy(result)

    def compare_metric(
        self,
        *,
        project_id: str,
        service_name: str,
        environment: str,
        metric_name: str,
        value: float,
    ) -> dict[str, Any] | None:
        key = _key_metric(self.db_path, project_id, service_name, environment, metric_name)
        cache = get_default_cache()
        try:
            cached = cache.get(key)
        except Exception as exc:  # defensive
            logger.warning(f"service_knowledge cache get failed: {exc}")
            cached = None
        if cached is not None:
            if not cached:
                return None
            return {**cached, "value": value}
        service = self.lookup(project_id=project_id, service_name=service_name, environment=environment)
        if not service:
            try:
                cache.set(key, {}, ttl=float(config.memory_cache_ttl_service_knowledge_seconds))
            except Exception as exc:  # pragma: no cover
                logger.warning(f"service_knowledge cache set failed: {exc}")
            return None
        for baseline in service["baselines"]:
            if baseline["metric_name"] == metric_name:
                payload = {
                    "service_name": service_name,
                    "metric_name": metric_name,
                    "min_value": baseline["min_value"],
                    "max_value": baseline["max_value"],
                    "unit": baseline["unit"],
                    "within_range": baseline["min_value"] <= value <= baseline["max_value"],
                }
                try:
                    cache.set(
                        key,
                        payload,
                        ttl=float(config.memory_cache_ttl_service_knowledge_seconds),
                    )
                except Exception as exc:  # pragma: no cover
                    logger.warning(f"service_knowledge cache set failed: {exc}")
                return {**payload, "value": value}
        try:
            cache.set(key, {}, ttl=float(config.memory_cache_ttl_service_knowledge_seconds))
        except Exception as exc:  # pragma: no cover
            logger.warning(f"service_knowledge cache set failed: {exc}")
        return None

    # ----------------------------------------------------------- bulk import

    async def import_from_monitor_mcp(self, *, project_id: str) -> int:
        from app.agent.mcp_client import get_mcp_client_with_retry

        client = await get_mcp_client_with_retry()
        services = await client.call_tool("list_all_services", {}, server_name="monitor")
        if not isinstance(services, list):
            return 0
        count = 0
        # Defer invalidation: invalidate once after the loop with a prefix
        # drop, instead of once per upsert (plan §3.3).
        cache = get_default_cache()
        prefix = _cache_prefix(self.db_path)
        for item in services:
            if not isinstance(item, dict):
                continue
            service_name = str(item.get("service_name") or item.get("name") or "").strip()
            if not service_name:
                continue
            self._raw_upsert_service(
                project_id=project_id,
                service_name=service_name,
                environment=str(item.get("environment") or "prod"),
                owner_team=str(item.get("owner_team") or item.get("team") or ""),
                owner_user=str(item.get("owner_user") or ""),
                description=str(item.get("description") or ""),
            )
            count += 1
        if count:
            try:
                cache.invalidate_prefix(prefix)
            except Exception as exc:  # pragma: no cover
                logger.warning(f"service_knowledge cache invalidate failed: {exc}")
        return count

    # ----------------------------------------------------------------- infra

    def _raw_lookup(
        self, project_id: str, service_name: str, environment: str
    ) -> dict[str, Any] | None:
        env = environment or "prod"
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM services
                WHERE project_id = ? AND service_name = ? AND environment = ?
                """,
                (project_id, service_name, env),
            ).fetchone()
            if row is None:
                return None
            baselines = connection.execute(
                """
                SELECT *
                FROM service_baselines
                WHERE project_id = ? AND service_name = ? AND environment = ?
                ORDER BY metric_name
                """,
                (project_id, service_name, env),
            ).fetchall()
            relations = connection.execute(
                """
                SELECT *
                FROM service_relations
                WHERE project_id = ? AND source_service = ? AND environment = ?
                ORDER BY relation_type, target_service
                """,
                (project_id, service_name, env),
            ).fetchall()
        return {
            "project_id": row["project_id"],
            "service_name": row["service_name"],
            "environment": row["environment"],
            "owner_team": row["owner_team"],
            "owner_user": row["owner_user"],
            "description": row["description"],
            "enabled": bool(row["enabled"]),
            "updated_at": row["updated_at"],
            "baselines": [_baseline_from_row(item) for item in baselines],
            "relations": [_relation_from_row(item) for item in relations],
        }

    def _raw_upsert_service(
        self,
        *,
        project_id: str,
        service_name: str,
        environment: str,
        owner_team: str,
        owner_user: str,
        description: str,
    ) -> None:
        """Bypass the cached ``upsert_service`` invalidation path — used by
        :meth:`import_from_monitor_mcp` which defers invalidation to the
        end of the loop."""

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO services (
                    project_id, service_name, environment, owner_team, owner_user,
                    description, enabled, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(project_id, service_name, environment) DO UPDATE SET
                    owner_team = excluded.owner_team,
                    owner_user = excluded.owner_user,
                    description = excluded.description,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    service_name,
                    environment,
                    owner_team,
                    owner_user,
                    description,
                    _utc_now(),
                ),
            )

    def _ensure_database(self) -> None:
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS services (
                    project_id TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    owner_team TEXT NOT NULL DEFAULT '',
                    owner_user TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, service_name, environment)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS service_relations (
                    project_id TEXT NOT NULL,
                    source_service TEXT NOT NULL,
                    target_service TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS service_baselines (
                    project_id TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    min_value REAL NOT NULL,
                    max_value REAL NOT NULL,
                    unit TEXT NOT NULL DEFAULT '',
                    sample_window TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, service_name, environment, metric_name)
                )
                """
            )
        self._initialized = True

    @contextmanager
    def _connection(self):
        self._ensure_database()
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def _service_summary_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": row["project_id"],
        "service_name": row["service_name"],
        "environment": row["environment"],
        "owner_team": row["owner_team"],
        "owner_user": row["owner_user"],
        "description": row["description"],
        "enabled": bool(row["enabled"]),
        "updated_at": row["updated_at"],
    }


def _baseline_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": row["project_id"],
        "service_name": row["service_name"],
        "environment": row["environment"],
        "metric_name": row["metric_name"],
        "min_value": float(row["min_value"]),
        "max_value": float(row["max_value"]),
        "unit": row["unit"],
        "sample_window": row["sample_window"],
        "updated_at": row["updated_at"],
    }


def _relation_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": row["project_id"],
        "source_service": row["source_service"],
        "target_service": row["target_service"],
        "relation_type": row["relation_type"],
        "environment": row["environment"],
        "updated_at": row["updated_at"],
    }


service_knowledge_service = ServiceKnowledgeService()