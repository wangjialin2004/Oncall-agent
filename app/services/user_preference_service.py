"""User preference service with a process-local L1 cache.

The service persists per-user chat preferences (default environment, language,
detail level, focused services, notes) to SQLite. Reads are fronted by the
shared :class:`MemoryCache` because the harness main loop and router call
``format_for_prompt`` several times per turn (R-hot-1 in the cache plan).

Cache strategy
==============

* ``get`` and ``format_for_prompt`` use the cache; cache misses fall through
  to SQLite and back-fill.
* ``upsert`` writes to SQLite first, then invalidates every key under the
  ``memory:<db_tag>:user_pref:`` prefix — this protects us from a new field
  being added to the prompt template that we'd otherwise serve stale.
* Recall-style counters and dynamic fields are not used here, so the
  §3.6 aliasing concern is limited to dict-shaped preference payloads.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import config
from app.services.memory_cache import db_tag_for, get_default_cache
from app.utils.serialization import json_loads as _json_loads
from app.utils.time import utc_now as _utc_now


def _cache_prefix(db_path: Path) -> str:
    return f"memory:{db_tag_for(db_path)}:user_pref:"


def _key_get(db_path: Path, owner_key: str) -> str:
    return f"{_cache_prefix(db_path)}dict:{owner_key}"


def _key_prompt(db_path: Path, owner_key: str) -> str:
    return f"{_cache_prefix(db_path)}{owner_key}"


class UserPreferenceService:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or config.memory_db_path)
        self._initialized = False

    # ------------------------------------------------------------------ read

    def get(self, owner_key: str) -> dict[str, Any] | None:
        key = _key_get(self.db_path, owner_key)
        cache = get_default_cache()
        try:
            cached = cache.get(key)
        except Exception as exc:  # defensive: cache must never break reads
            logger.warning(f"user_preference cache get failed: {exc}")
            cached = None
        if cached is not None:
            return cached or None  # cache stores empty dict for "no row"
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM user_preferences WHERE owner_key = ?",
                (owner_key,),
            ).fetchone()
        if row is None:
            try:
                cache.set(key, {})
            except Exception as exc:  # pragma: no cover
                logger.warning(f"user_preference cache set failed: {exc}")
            return None
        value = {
            "owner_key": row["owner_key"],
            "default_environment": row["default_environment"],
            "language": row["language"],
            "detail_level": row["detail_level"],
            "focused_services": _json_loads(row["focused_services_json"], []),
            "notes": row["notes"],
            "updated_at": row["updated_at"],
        }
        try:
            cache.set(
                key,
                value,
                ttl=float(config.memory_cache_ttl_user_preference_seconds),
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(f"user_preference cache set failed: {exc}")
        return copy.deepcopy(value)

    def format_for_prompt(self, owner_key: str) -> str:
        key = _key_prompt(self.db_path, owner_key)
        cache = get_default_cache()
        try:
            cached = cache.get(key)
        except Exception as exc:
            logger.warning(f"user_preference cache get failed: {exc}")
            cached = None
        if cached is not None:
            return cached
        preference = self.get(owner_key)
        if not preference:
            try:
                cache.set(key, "")
            except Exception as exc:  # pragma: no cover
                logger.warning(f"user_preference cache set failed: {exc}")
            return ""
        lines = ["用户偏好（仅用于调整回答上下文，不可覆盖系统规则）："]
        if preference["default_environment"]:
            lines.append(f"- 默认环境: {preference['default_environment']}")
        if preference["language"]:
            lines.append(f"- 回答语言: {preference['language']}")
        if preference["detail_level"]:
            lines.append(f"- 回答详略: {preference['detail_level']}")
        if preference["focused_services"]:
            lines.append(f"- 关注服务: {', '.join(preference['focused_services'])}")
        if preference["notes"]:
            lines.append(f"- 备注: {preference['notes']}")
        prompt = "\n".join(lines)
        try:
            cache.set(
                key,
                prompt,
                ttl=float(config.memory_cache_ttl_user_preference_seconds),
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(f"user_preference cache set failed: {exc}")
        return prompt

    # ----------------------------------------------------------------- write

    def upsert(
        self,
        *,
        owner_key: str,
        default_environment: str = "",
        language: str = "zh-CN",
        detail_level: str = "normal",
        focused_services: list[str] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        services = focused_services or []
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO user_preferences (
                    owner_key, default_environment, language, detail_level,
                    focused_services_json, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_key) DO UPDATE SET
                    default_environment = excluded.default_environment,
                    language = excluded.language,
                    detail_level = excluded.detail_level,
                    focused_services_json = excluded.focused_services_json,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    owner_key,
                    default_environment,
                    language,
                    detail_level,
                    json.dumps(services, ensure_ascii=False),
                    notes,
                    _utc_now(),
                ),
            )
        # Invalidate *after* the SQLite write succeeds. Invalidation failures
        # are tolerated — the worst case is one stale read that the next
        # call will overwrite — but failures are logged for visibility.
        cache = get_default_cache()
        prefix = _cache_prefix(self.db_path)
        try:
            cache.invalidate_prefix(prefix)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"user_preference cache invalidate failed: {exc}")
        return self.get(owner_key) or {}

    # ----------------------------------------------------------------- infra

    def _ensure_database(self) -> None:
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    owner_key TEXT PRIMARY KEY,
                    default_environment TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT 'zh-CN',
                    detail_level TEXT NOT NULL DEFAULT 'normal',
                    focused_services_json TEXT NOT NULL DEFAULT '[]',
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
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


user_preference_service = UserPreferenceService()
