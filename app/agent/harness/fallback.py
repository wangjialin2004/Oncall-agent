from __future__ import annotations

from app.config import config

def get_expert(route: str):
    from app.agent.harness import loop as harness_loop
    fn = getattr(harness_loop, "get_expert", None)
    if fn is not None and getattr(fn, "__module__", "") != __name__:
        return fn(route)
    from app.agent.experts.registry import get_expert as _impl
    return _impl(route)


from collections.abc import AsyncGenerator, Sequence
from typing import Any

from loguru import logger

from app.agent.events import make_agent_event
from app.agent.stream_common import TIMELINE_EVENT_TYPES, build_timeout_report

class HarnessFallbackMixin:
    """Knowledge / raw-vector fallback paths when the main harness fails."""

    async def _fallback_stream(
        self,
        *,
        message: str,
        session_id: str,
        owner_key: str,
        reason: str,
        seed_events: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        events = [
            dict(event)
            for event in (seed_events or [])
            if event.get("type") in TIMELINE_EVENT_TYPES
        ]
        if events:
            yield events[-1]

        start_event = make_agent_event(
            agent="harness",
            stage="fallback_start",
            status="degraded",
            summary="Harness main flow unavailable; falling back to knowledge and raw vector.",
            payload={"reason": reason, "levels": ["knowledge_expert", "raw_vector"]},
            trace_id=session_id,
            span_id=f"harness:{session_id}:fallback",
        )
        events.append(start_event)
        yield start_event

        answer_parts: list[str] = []
        try:
            async for event in self._iter_knowledge_fallback(
                message=message,
                session_id=session_id,
                owner_key=owner_key,
            ):
                event_type = event.get("type")
                if event_type == "content":
                    answer_parts.append(str(event.get("data") or ""))
                elif event_type in TIMELINE_EVENT_TYPES:
                    events.append(event)
                yield event
        except Exception as exc:
            fail_event = make_agent_event(
                agent="harness",
                stage="knowledge_fallback_error",
                status="degraded",
                summary=f"knowledge_expert fallback failed: {exc}",
                payload={"error": str(exc)},
                trace_id=session_id,
                span_id=f"harness:{session_id}:fallback:knowledge",
            )
            events.append(fail_event)
            yield fail_event

        answer = "".join(answer_parts).strip()
        if answer:
            complete_event = make_agent_event(
                agent="harness",
                stage="fallback_complete",
                status="completed",
                summary="knowledge_expert returned fallback answer.",
                payload={"level": "knowledge_expert", "answer_chars": len(answer)},
                trace_id=session_id,
                span_id=f"harness:{session_id}:fallback",
            )
            events.append(complete_event)
            yield complete_event
            yield {
                "type": "complete",
                "route": "knowledge",
                "route_reason": f"{reason}:fallback_knowledge_expert",
                "answer": answer,
                "case_id": "",
                "events": events,
            }
            return

        empty_event = make_agent_event(
            agent="harness",
            stage="knowledge_fallback_empty",
            status="degraded",
            summary="knowledge_expert produced no usable answer; falling back to raw vector.",
            payload={"reason": reason},
            trace_id=session_id,
            span_id=f"harness:{session_id}:fallback:knowledge",
        )
        events.append(empty_event)
        yield empty_event

        raw_answer = await self._raw_vector_fallback_answer(message)
        raw_event = make_agent_event(
            agent="harness",
            stage="raw_vector_fallback_complete",
            status="completed" if raw_answer else "degraded",
            summary="Raw vector fallback returned a final result." if raw_answer else "Raw vector fallback returned no result.",
            payload={"answer_chars": len(raw_answer)},
            trace_id=session_id,
            span_id=f"harness:{session_id}:fallback:vector",
        )
        events.append(raw_event)
        yield raw_event

        final_answer = raw_answer or build_timeout_report(
            subject="Harness main loop and knowledge fallback",
            message=message,
            timeout_seconds=self.limits.timeout_seconds,
        )
        yield {"type": "content", "data": final_answer, "agent": "raw_vector_fallback"}
        yield {
            "type": "complete",
            "route": "knowledge",
            "route_reason": f"{reason}:fallback_raw_vector",
            "answer": final_answer,
            "case_id": "",
            "events": events,
        }

    async def _iter_knowledge_fallback(
        self, *, message: str, session_id: str, owner_key: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        expert = self.fallback_expert or get_expert("knowledge")
        context = ""
        if owner_key and getattr(config, "user_preferences_enabled", False):
            from app.services.user_preference_service import user_preference_service

            context = user_preference_service.format_for_prompt(owner_key)
        generator = expert.run(
            message=message,
            session_id=session_id,
            trace_id=session_id,
            context=context,
        )
        try:
            async for event in generator:
                yield event
        finally:
            aclose = getattr(generator, "aclose", None)
            if aclose:
                await aclose()

    async def _raw_vector_fallback_answer(self, message: str) -> str:
        try:
            searcher = self.vector_searcher
            if searcher is None:
                from app.services.vector_search_service import vector_search_service

                searcher = vector_search_service
            results = searcher.search(message, top_k=getattr(config, "rag_top_k", 3))
        except Exception as exc:
            logger.warning(f"raw vector 闄嶇骇妫€绱㈠け璐ワ細{exc}")
            return ""

        if not results:
            return ""

        lines = ["# Raw Vector Fallback Results", "", "Harness and knowledge_expert are unavailable; raw vector matches follow:"]
        for index, result in enumerate(results, 1):
            content = str(getattr(result, "content", "") or "").strip()
            if not content:
                continue
            source = str(getattr(result, "source", "") or "unknown source")
            score = getattr(result, "score", "")
            rank = getattr(result, "rank", index)
            snippet = content[:800]
            lines.extend(
                [
                    "",
                    f"## Reference {index}",
                    f"- Source: {source}",
                    f"- Rank: {rank}",
                    f"- Score: {score}",
                    "",
                    snippet,
                ]
            )
        return "\n".join(lines).strip()

    def _final_fallback_complete_event(
        self,
        *,
        message: str,
        session_id: str,
        reason: str,
        timeout_seconds: float,
        seed_events: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build a minimal ``complete`` event when both the main loop and the
        full knowledge/vector fallback have already blown their budgets.

        Avoids a hanging SSE connection by emitting one final ``complete`` event
        with a ``build_timeout_report``-style payload.
        """
        events = [
            dict(event)
            for event in (seed_events or [])
            if event.get("type") in TIMELINE_EVENT_TYPES
        ]
        fallback_text = build_timeout_report(
            subject="Harness main loop and knowledge fallback",
            message=message,
            timeout_seconds=timeout_seconds,
        )
        events.append(
            make_agent_event(
                agent="harness",
                stage="fallback_timeout",
                status="degraded",
                summary=f"Fallback path exceeded {timeout_seconds:g}s; returned final fallback report.",
                payload={"reason": reason, "timeout_seconds": timeout_seconds},
                trace_id=session_id,
                span_id=f"harness:{session_id}:fallback_timeout",
            )
        )
        return {
            "type": "complete",
            "route": "knowledge",
            "route_reason": f"{reason}:fallback_timeout",
            "answer": fallback_text,
            "case_id": "",
            "events": events,
        }

    # ---------------------------------------------------------- checkpoint
