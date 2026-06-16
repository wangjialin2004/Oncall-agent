import pytest
from pydantic import ValidationError

from backend.models import AgentStreamRequest
from backend.services.agent_router import AgentRoute, AgentRouter


def test_explicit_rag_mode_routes_to_rag():
    router = AgentRouter()

    route = router.resolve_route(message="explain the runbook", mode="rag")

    assert route == AgentRoute(route="rag", reason="explicit_mode")


def test_explicit_oncall_mode_routes_to_oncall():
    router = AgentRouter()

    route = router.resolve_route(message="checkout-api is slow", mode="oncall")

    assert route == AgentRoute(route="oncall", reason="explicit_mode")


def test_auto_mode_uses_aiops_intent_for_incident_text():
    router = AgentRouter()

    route = router.resolve_route(message="CPU alert on checkout-api", mode="auto")

    assert route.route == "oncall"
    assert route.reason in {"matched_aiops_keyword", "llm_semantic_aiops"}


def test_auto_mode_defaults_to_rag_for_knowledge_text():
    router = AgentRouter()

    route = router.resolve_route(message="explain the deployment document", mode="auto")

    assert route.route == "rag"


def test_invalid_mode_is_rejected_by_pydantic():
    with pytest.raises(ValidationError):
        AgentStreamRequest(session_id="s1", message="hello", mode="bad-mode")
