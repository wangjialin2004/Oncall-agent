"""
State definitions for the AIOps and OnCall diagnosis workflows.
"""

import operator
from typing import Annotated, Any, NotRequired, TypedDict


class PlanExecuteState(TypedDict):
    """Existing Plan-Execute-Replan state."""

    input: str
    plan: list[Any]
    past_steps: Annotated[list[tuple], operator.add]
    response: str
    session_id: NotRequired[str]
    case_id: NotRequired[str]


class OnCallState(TypedDict):
    """Supervisor-orchestrated OnCall multi-agent state."""

    input: str
    session_id: str
    case_id: str
    route: NotRequired[str]
    route_reason: NotRequired[str]
    incident: NotRequired[dict[str, Any]]
    plan: list[dict[str, Any]]
    past_steps: Annotated[list[dict[str, Any]], operator.add]
    evidence: Annotated[list[dict[str, Any]], operator.add]
    diagnosis: NotRequired[dict[str, Any]]
    response: str
    iteration: int
    max_iterations: int
    events: list[dict[str, Any]]
