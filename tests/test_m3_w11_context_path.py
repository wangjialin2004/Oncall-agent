"""M3 W11: dual-path context smoke (import + switch presence)."""

from __future__ import annotations


def test_agent_context_state_importable():
    from app.agent.context.state import AgentContextState

    assert AgentContextState is not None


def test_legacy_context_builder_still_importable():
    # Must remain importable for rebuild/fallback (not deleted in W11).
    from app.agent.harness.context import ContextBuilder, HarnessContext

    assert ContextBuilder is not None
    assert HarnessContext is not None


def test_config_has_stateful_and_rebuild_flags():
    from app.config import config

    assert hasattr(config, "harness_stateful_context_enabled")
    assert hasattr(config, "harness_context_rebuild_from_turns_enabled")
    assert hasattr(config, "harness_trace_sample_rate")
    assert hasattr(config, "otel_exporter_otlp_endpoint")
