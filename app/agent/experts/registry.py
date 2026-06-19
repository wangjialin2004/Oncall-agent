"""Route → expert mapping for the Router + Expert Agents architecture."""

from __future__ import annotations

from app.agent.experts.base import ExpertAgent
from app.agent.experts.change import change_expert
from app.agent.experts.diagnosis import diagnosis_expert
from app.agent.experts.knowledge import knowledge_expert
from app.agent.experts.log import log_expert
from app.agent.experts.metric import metric_expert

# The five expert routes. ``diagnosis`` is the safe default for cross-domain or
# low-confidence cases (it investigates broadly via its own wide tool set).
EXPERT_REGISTRY: dict[str, ExpertAgent] = {
    "knowledge": knowledge_expert,
    "metric": metric_expert,
    "log": log_expert,
    "change": change_expert,
    "diagnosis": diagnosis_expert,
}

EXPERT_ROUTES = tuple(EXPERT_REGISTRY.keys())
DEFAULT_ROUTE = "diagnosis"


def get_expert(route: str) -> ExpertAgent:
    """Return the expert for a route, falling back to comprehensive diagnosis."""
    return EXPERT_REGISTRY.get(route, EXPERT_REGISTRY[DEFAULT_ROUTE])
