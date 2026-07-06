"""Hand-rolled async Redis fake used by the harness checkpoint tests.

We deliberately avoid third-party mock libraries (per project convention). The
fake implements just enough of ``redis.asyncio.Redis`` to drive the checkpoint
store: ``get`` / ``set`` / ``delete`` / ``scan`` plus a ``pipeline`` context
manager that flushes a batch of commands.
"""

from __future__ import annotations

import fnmatch
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Entry:
    value: str
    expires_at: float | None = None


@dataclass
class FakePipeline:
    parent: FakeRedis
    commands: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def set(self, key: str, value: Any, ex: int | None = None) -> FakePipeline:
        self.commands.append(("set", (key, value, ex)))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for op, args in self.commands:
            if op == "set":
                key, value, ex = args
                self.parent._set_sync(key, value, ex=ex)
                results.append(True)
        self.commands.clear()
        return results


@dataclass
class FakeRedis:
    data: dict[str, _Entry] = field(default_factory=dict)
    fail_on: set[str] = field(default_factory=set)
    now: float = 1_000_000.0

    def _now(self) -> float:
        return self.now

    def _set_sync(self, key: str, value: Any, *, ex: int | None) -> None:
        if "set" in self.fail_on:
            raise ConnectionError("fake: set disabled")
        expires_at = self._now() + ex if ex else None
        self.data[key] = _Entry(value=str(value), expires_at=expires_at)

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        if "set" in self.fail_on:
            raise ConnectionError("fake: set disabled")
        self._set_sync(key, value, ex=ex)
        return True

    async def get(self, key: str) -> str | None:
        if "get" in self.fail_on:
            raise ConnectionError("fake: get disabled")
        entry = self.data.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and entry.expires_at <= self._now():
            self.data.pop(key, None)
            return None
        return entry.value

    async def delete(self, *keys: str) -> int:
        if "delete" in self.fail_on:
            raise ConnectionError("fake: delete disabled")
        removed = 0
        for key in keys:
            if key in self.data:
                self.data.pop(key, None)
                removed += 1
        return removed

    async def scan(self, cursor: int, match: str, count: int = 100) -> tuple[int, list[str]]:
        # We ignore count and return the whole match in one batch — fine for
        # tests since we never have many keys per session.
        keys = [k for k in self.data if fnmatch.fnmatchcase(k, match)]
        return (0, keys)

    @asynccontextmanager
    async def pipeline(self, transaction: bool = False) -> AsyncIterator[FakePipeline]:
        yield FakePipeline(parent=self)

    async def ping(self) -> bool:
        if "ping" in self.fail_on:
            raise ConnectionError("fake: ping disabled")
        return True

    async def aclose(self) -> None:
        return None
