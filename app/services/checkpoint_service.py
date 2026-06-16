"""LangGraph checkpoint persistence helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def create_sync_sqlite_checkpointer(db_path: str | Path) -> SqliteSaver:
    """Create a sync SQLite checkpointer for sync-only management calls."""

    import sqlite3

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    checkpointer.setup()
    return checkpointer


def create_sqlite_checkpointer(db_path: str | Path) -> AsyncSqliteSaver:
    """Create an async SQLite-backed LangGraph checkpointer."""

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = aiosqlite.connect(str(path))
    checkpointer = AsyncSqliteSaver(connection)
    checkpointer._connection_started = False
    return checkpointer


async def setup_checkpointer(checkpointer: object) -> None:
    """Initialize async checkpoint storage before first graph use."""

    connection = getattr(checkpointer, "conn", None)
    if connection is not None and not getattr(checkpointer, "_connection_started", True):
        await connection
        checkpointer._connection_started = True

    setup = getattr(checkpointer, "setup", None)
    if setup is None:
        return
    result = setup()
    if asyncio.iscoroutine(result):
        await result


async def aclose_checkpointer(checkpointer: object) -> None:
    """Close a checkpointer connection from async code."""

    connection = getattr(checkpointer, "conn", None)
    if connection is None:
        return
    close = getattr(connection, "close", None)
    if close is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result


def close_checkpointer(checkpointer: object) -> None:
    """Close a checkpointer connection when the implementation exposes one."""

    connection = getattr(checkpointer, "conn", None)
    if connection is None:
        return
    close = getattr(connection, "close", None)
    if close is None:
        return
    result: Any = close()
    if asyncio.iscoroutine(result):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(result)
        else:
            loop.create_task(result)
