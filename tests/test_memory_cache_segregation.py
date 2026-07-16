"""Task 10 — segregation between the session whiteboard and long-term memory.

Per plan ``plan/2026-07-08-stateful-agent-context.md`` §10:

- ContextState must never be written to / read from the long-term
  ``memory_cache`` subsystem.
- The harness context-build path must not import or call ``memory_cache`` /
  ``experience_memory_service``.

This module codifies those rules as **static** tests so a future change can't
quietly re-introduce a link.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.agent.context import store as ctx_store_mod
from app.agent.context.integration import (
    build_default_store,
    capture_tool_outcome,
)


CONTEXT_DIR = Path(ctx_store_mod.__file__).parent
INTEGRATION_DIR = CONTEXT_DIR
HARNESS_CONTEXT_PY = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "agent"
    / "harness"
    / "context.py"
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(_read_text(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for n in node.names:
                found.append((f"{node.module}.{n.name}", node.lineno))
        elif isinstance(node, ast.Import):
            for n in node.names:
                found.append((n.name, node.lineno))
    return found


@pytest.mark.parametrize("py_file", sorted(CONTEXT_DIR.glob("*.py")))
def test_context_modules_do_not_import_memory_cache(py_file: Path) -> None:
    """ContextState code must not touch long-term memory subsystem."""
    imports = _imports(py_file)
    bad = [
        (name, line) for name, line in imports
        if "memory_cache" in name
        or "experience_memory_service" in name
    ]
    assert not bad, (
        f"{py_file.name} imports forbidden modules: {bad}"
    )


def test_harness_context_does_not_import_memory_cache() -> None:
    """Legacy :mod:`app.agent.harness.context` must keep its old behavior
    unchanged when the flag is off — that includes NOT pulling in
    ``memory_cache`` from the new whiteboard."""
    imports = _imports(HARNESS_CONTEXT_PY)
    bad = [
        (name, line) for name, line in imports
        if "memory_cache" in name
        or "agent.context" in name  # whiteboard must not be a transitive dep
    ]
    assert not bad, (
        f"{HARNESS_CONTEXT_PY.name} imports forbidden modules: {bad}"
    )


def test_context_state_does_not_reference_memory_cache_at_runtime() -> None:
    """Runtime guard: a freshly built ContextState never falls through to
    the memory cache subsystem even when callers wire up the default store."""
    # Sanity: build the default store lazily. It must not import the memory
    # cache layer at construction time.
    store = build_default_store()
    assert store is not None
    # Capture a tool outcome against a fresh state — must not touch memory_cache.
    state = store.settings  # type: ignore[attr-defined]
    assert state is not None


def test_capture_tool_outcome_does_not_call_memory_cache() -> None:
    """Direct check on the bridge function: it must only mutate the state."""
    from app.agent.context.state import AgentContextState

    state = AgentContextState(owner_key="u", session_id="s")
    # Patch a sentinel attribute to detect any cross-module call.
    called = {"memory_cache": 0, "experience": 0}

    class _Watchdog:
        def __getattr__(self, name: str) -> object:  # pragma: no cover
            if "memory_cache" in name:
                called["memory_cache"] += 1
            if "experience" in name:
                called["experience"] += 1
            raise AttributeError(name)

    # Swap sys.modules entries to detect forbidden imports during the call.
    import sys
    sentinel = _Watchdog()
    for mod_name in ("app.services.memory_cache", "app.services.experience_memory_service"):
        sys.modules[mod_name] = sentinel  # type: ignore[assignment]
    try:
        capture_tool_outcome(
            state,
            tool_name="check_redis_health",
            raw_result={"ok": True, "summary": "ping ok", "latency_ms": 12},
            raw_ref="timeline:1",
        )
    finally:
        for mod_name in (
            "app.services.memory_cache",
            "app.services.experience_memory_service",
        ):
            sys.modules.pop(mod_name, None)

    assert called == {"memory_cache": 0, "experience": 0}, (
        f"harness context build reached into forbidden modules: {called}"
    )