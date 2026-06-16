from __future__ import annotations

from dataclasses import dataclass

from app.services.router_service import RouterService
from backend.models import AgentMode, ResolvedAgentRoute


@dataclass(frozen=True, slots=True)
class AgentRoute:
    route: ResolvedAgentRoute
    reason: str


class AgentRouter:
    """Resolve frontend-selected mode into an executable agent route."""

    def __init__(self, router_service: RouterService | None = None):
        self.router_service = router_service or RouterService()

    def resolve_route(self, *, message: str, mode: AgentMode) -> AgentRoute:
        if mode == "rag":
            return AgentRoute(route="rag", reason="explicit_mode")
        if mode == "oncall":
            return AgentRoute(route="oncall", reason="explicit_mode")

        decision = self.router_service.route_message(message)
        if decision.route == "aiops":
            return AgentRoute(route="oncall", reason=decision.reason)
        return AgentRoute(route="rag", reason=decision.reason)
