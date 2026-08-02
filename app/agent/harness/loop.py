"""Unified feature-flagged harness loop (facade).

Heavy helpers live in mixins under ``app.agent.harness.*``. Public API:
``HarnessService`` / ``harness_service``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from loguru import logger

from app.agent.agent_loop import GuardedToolExecutor

# Re-exports so existing tests can monkeypatch ``app.agent.harness.loop.*``
# and still affect mixin modules that bind symbols via this facade.
from app.agent.context.integration import (  # noqa: E402
    build_stateful_context,
    persist_stateful_context,
    stateful_context_enabled,
)
from app.agent.events import make_agent_event
from app.agent.experts.registry import get_expert  # noqa: E402
from app.agent.harness.checkpoint_ops import HarnessCheckpointOpsMixin
from app.agent.harness.clarifier import MissingParameterClarifier
from app.agent.harness.close_path import HarnessClosePathMixin
from app.agent.harness.context import ContextBuilder
from app.agent.harness.events_emit import HarnessEventsMixin
from app.agent.harness.fallback import HarnessFallbackMixin
from app.agent.harness.llm_turns import HarnessLlmTurnsMixin
from app.agent.harness.planner import LightweightPlanner
from app.agent.harness.policy import HarnessPolicyMixin
from app.agent.harness.registry import HarnessToolRegistry
from app.agent.harness.state import HarnessLimits, HarnessState
from app.agent.harness.stream_inner import HarnessStreamInnerMixin
from app.agent.harness.tools_runtime import HarnessToolsRuntimeMixin
from app.agent.harness.verifier import EvidenceVerifier
from app.config import config
from app.core.metrics import observe_agent_run
from app.core.runtime_tools import RuntimeTool
from app.services.context_repository import (
    ContextRepository,
    build_default_context_repository,
)
from app.services.harness_checkpoint import (
    HarnessCheckpointStore,
    get_default_checkpoint_store,
    is_checkpoint_active,
)
from app.services.router_service import RouterService


class HarnessService(
    HarnessPolicyMixin,
    HarnessLlmTurnsMixin,
    HarnessEventsMixin,
    HarnessToolsRuntimeMixin,
    HarnessClosePathMixin,
    HarnessFallbackMixin,
    HarnessCheckpointOpsMixin,
    HarnessStreamInnerMixin,
):
    """A single orchestrating loop behind ``harness_enabled``.

    Behaviour helpers live in mixins; this class owns construction and ``stream``.
    """

    def __init__(
        self,
        *,
        context_builder: ContextBuilder | None = None,
        router: RouterService | None = None,
        llm_client: Any | None = None,
        tools: Sequence[RuntimeTool] | None = None,
        limits: HarnessLimits | None = None,
        fallback_expert: Any | None = None,
        vector_searcher: Any | None = None,
        checkpoint_store: HarnessCheckpointStore | None = None,
        context_store: Any | None = None,
        context_repository: ContextRepository | None = None,
    ) -> None:
        self.context_builder = context_builder or ContextBuilder()
        self.router = router or RouterService()
        self.llm_client = llm_client
        self.tool_registry = HarnessToolRegistry(list(tools) if tools is not None else None)
        self.tool_executor = GuardedToolExecutor()
        self.planner = LightweightPlanner()
        self.clarifier = MissingParameterClarifier()
        self.verifier = EvidenceVerifier()
        self.fallback_expert = fallback_expert
        self.vector_searcher = vector_searcher
        self.context_store = context_store
        self.context_repository = context_repository or build_default_context_repository()
        self._checkpoint_sessions_scheduled: set[tuple[str, str, str]] = set()
        self._checkpoint_tasks: dict[
            tuple[str, str, str], set[asyncio.Task[Any]]
        ] = {}
        # Default to the global checkpoint store only when every feature flag
        # says so; otherwise None disables checkpointing without extra wiring.
        self.checkpoint_store: HarnessCheckpointStore | None = (
            checkpoint_store
            if checkpoint_store is not None
            else (get_default_checkpoint_store() if is_checkpoint_active() else None)
        )
        self.limits = limits or HarnessLimits(
            max_steps=int(getattr(config, "harness_max_steps", 6)),
            token_budget=int(getattr(config, "harness_token_budget", 16000)),
            timeout_seconds=float(getattr(config, "harness_timeout_seconds", 150.0)),
            step_timeout_seconds=float(
                getattr(config, "harness_step_timeout_seconds", 25.0)
            ),
            fallback_timeout_seconds=float(
                getattr(config, "harness_fallback_timeout_seconds", 30.0)
            ),
            no_progress_limit=int(getattr(config, "harness_no_progress_limit", 2)),
        )
        # Per-request message body budget (separate from cumulative token_budget).
        self.message_token_budget = int(getattr(config, "harness_message_token_budget", 60000))
        self._warn_if_planner_model_unset()

    async def stream(
        self,
        message: str,
        session_id: str,
        owner_key: str = "",
        checkpoint_replay: bool | None = None,
        attachment_refs: list[dict[str, Any]] | None = None,
        simulate: str | None = None,
        prefer_parallel: bool | None = None,
        raw_question: str | None = None,
        context_run_id: str | None = None,
        completion_committer: Any | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        # Shared with _stream_inner so outer TimeoutError can best-effort checkpoint.
        runtime: dict[str, Any] = {
            "state": None,
            "messages": [],
            "context_state": None,
            "started": time.perf_counter(),
            "soft_closed": False,
            "completion_committer": completion_committer,
            "unified_commit_done": False,
            "context_run_id": context_run_id or session_id,
        }
        try:
            async with asyncio.timeout(self.limits.timeout_seconds):
                async for event in self._stream_inner(
                    message,
                    session_id,
                    owner_key,
                    checkpoint_replay=checkpoint_replay,
                    attachment_refs=attachment_refs,
                    raw_question=raw_question,
                    context_run_id=context_run_id or session_id,
                    runtime=runtime,
                    simulate=simulate,
                    prefer_parallel=prefer_parallel,
                ):
                    if (
                        isinstance(event, dict)
                        and event.get("type") == "complete"
                        and callable(runtime.get("completion_committer"))
                        and not runtime.get("unified_commit_done")
                    ):
                        try:
                            await runtime["completion_committer"](
                                event,
                                runtime.get("context_state"),
                            )
                            runtime["unified_commit_done"] = True
                            # Marker for the API's unified terminal commit without
                            # changing SSE type semantics.
                            event = dict(event)
                            event["_unified_context_commit_attempted"] = True
                            event["_unified_context_committed"] = True
                        except Exception as exc:  # noqa: BLE001 — answer still completes
                            logger.warning(
                                f"unified context commit failed (degraded): {exc}"
                            )
                            event = dict(event)
                            event["_unified_context_commit_attempted"] = True
                            event["_unified_context_committed"] = False
                            event["_unified_context_commit_error"] = type(exc).__name__
                    yield event
        except TimeoutError:
            logger.warning(f"harness execution timed out {self.limits.timeout_seconds}s; returning fallback")
            timeout_state = runtime.get("state")
            if isinstance(timeout_state, HarnessState):
                self._schedule_checkpoint_save(
                    state=timeout_state,
                    messages=list(runtime.get("messages") or []),
                    step_index=max(1, int(timeout_state.step or 0)),
                    tool_calls=[],
                    context_state=runtime.get("context_state"),
                )
            # M3 W9: if we already collected investigation evidence, soft-close
            # from it instead of emitting degraded fallback markers.
            if self._should_timeout_soft_close(timeout_state):
                soft_event = self._soft_close_timeout_event(
                    state=timeout_state,
                    session_id=session_id,
                    message=message,
                    timeout_seconds=self.limits.timeout_seconds,
                )
                runtime["soft_closed"] = True
                for event in soft_event.get("_yield_events") or []:
                    yield event
                complete_event = soft_event["complete"]
                if (
                    callable(runtime.get("completion_committer"))
                    and not runtime.get("unified_commit_done")
                ):
                    try:
                        await runtime["completion_committer"](
                            complete_event,
                            runtime.get("context_state"),
                        )
                        runtime["unified_commit_done"] = True
                        complete_event = dict(complete_event)
                        complete_event["_unified_context_commit_attempted"] = True
                        complete_event["_unified_context_committed"] = True
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"unified context commit failed (degraded): {exc}")
                        complete_event = dict(complete_event)
                        complete_event["_unified_context_commit_attempted"] = True
                        complete_event["_unified_context_committed"] = False
                yield complete_event
                observe_agent_run(
                    status="degraded",
                    latency_seconds=time.perf_counter() - float(runtime.get("started") or time.perf_counter()),
                )
                return
            timeout_event = make_agent_event(
                agent="harness",
                stage="timeout_fallback",
                status="degraded",
                summary="Harness main loop timed out; returning fallback result.",
                payload={"timeout_seconds": self.limits.timeout_seconds},
                trace_id=session_id,
            )
            try:
                async with asyncio.timeout(self.limits.fallback_timeout_seconds):
                    async for event in self._fallback_stream(
                        message=message,
                        session_id=session_id,
                        owner_key=owner_key,
                        reason="harness_timeout",
                        seed_events=[timeout_event],
                    ):
                        if (
                            isinstance(event, dict)
                            and event.get("type") == "complete"
                            and callable(runtime.get("completion_committer"))
                            and not runtime.get("unified_commit_done")
                        ):
                            try:
                                await runtime["completion_committer"](
                                    event,
                                    runtime.get("context_state"),
                                )
                                runtime["unified_commit_done"] = True
                                event = dict(event)
                                event["_unified_context_commit_attempted"] = True
                                event["_unified_context_committed"] = True
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    f"unified context commit failed (degraded): {exc}"
                                )
                                event = dict(event)
                                event["_unified_context_commit_attempted"] = True
                                event["_unified_context_committed"] = False
                        yield event
            except TimeoutError:
                logger.warning(
                    f"harness fallback path also timed out after {self.limits.fallback_timeout_seconds}s; returning final fallback"
                )
                final_event = self._final_fallback_complete_event(
                    message=message,
                    session_id=session_id,
                    reason="fallback_timeout",
                    timeout_seconds=self.limits.fallback_timeout_seconds,
                    seed_events=[timeout_event],
                )
                if (
                    callable(runtime.get("completion_committer"))
                    and not runtime.get("unified_commit_done")
                ):
                    try:
                        await runtime["completion_committer"](
                            final_event,
                            runtime.get("context_state"),
                        )
                        runtime["unified_commit_done"] = True
                        final_event = dict(final_event)
                        final_event["_unified_context_commit_attempted"] = True
                        final_event["_unified_context_committed"] = True
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"unified context commit failed (degraded): {exc}")
                        final_event = dict(final_event)
                        final_event["_unified_context_commit_attempted"] = True
                        final_event["_unified_context_committed"] = False
                yield final_event
            observe_agent_run(
                status="timeout",
                latency_seconds=time.perf_counter() - float(runtime.get("started") or time.perf_counter()),
            )
        finally:
            await self._drain_checkpoint_tasks(
                owner_key=owner_key,
                session_id=session_id,
                trace_id=session_id,
            )

harness_service = HarnessService()

__all__ = [
    "HarnessService",
    "build_stateful_context",
    "get_expert",
    "harness_service",
    "persist_stateful_context",
    "stateful_context_enabled",
]
