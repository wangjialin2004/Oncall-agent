"""Router + Expert Agents package.

A flat multi-agent model: the router classifies each request into exactly one
expert route, and that expert answers. Every expert — including the
comprehensive-diagnosis expert — shares the streaming tool-calling loop in
base.py; diagnosis simply gets a broader cross-domain tool set.
"""

from app.agent.experts.base import ExpertAgent, ToolCallingExpert
from app.agent.experts.registry import (
    DEFAULT_ROUTE,
    EXPERT_REGISTRY,
    EXPERT_ROUTES,
    get_expert,
)

__all__ = [
    "ExpertAgent",
    "ToolCallingExpert",
    "EXPERT_REGISTRY",
    "EXPERT_ROUTES",
    "DEFAULT_ROUTE",
    "get_expert",
]
