"""Local tools for experts."""

from app.tools.change_tool import query_recent_changes
from app.tools.knowledge_tool import retrieve_knowledge
from app.tools.lookup_service_knowledge import lookup_service_knowledge
from app.tools.query_metrics_alerts import query_prometheus_alerts
from app.tools.recall_experience import recall_experience
from app.tools.time_tool import get_current_time

DEFAULT_LOCAL_AGENT_TOOLS = (
    retrieve_knowledge,
    recall_experience,
    get_current_time,
    query_prometheus_alerts,
)

KNOWLEDGE_LOCAL_TOOLS = (
    retrieve_knowledge,
    recall_experience,
    get_current_time,
)
METRIC_LOCAL_TOOLS = (
    query_prometheus_alerts,
    recall_experience,
    lookup_service_knowledge,
    get_current_time,
)
LOG_LOCAL_TOOLS = (
    recall_experience,
    lookup_service_knowledge,
    get_current_time,
)
CHANGE_LOCAL_TOOLS = (
    query_recent_changes,
    retrieve_knowledge,
    recall_experience,
    get_current_time,
)
DIAGNOSIS_LOCAL_TOOLS = (
    retrieve_knowledge,
    recall_experience,
    lookup_service_knowledge,
    get_current_time,
    query_prometheus_alerts,
    query_recent_changes,
)

__all__ = [
    "DEFAULT_LOCAL_AGENT_TOOLS",
    "KNOWLEDGE_LOCAL_TOOLS",
    "METRIC_LOCAL_TOOLS",
    "LOG_LOCAL_TOOLS",
    "CHANGE_LOCAL_TOOLS",
    "DIAGNOSIS_LOCAL_TOOLS",
    "retrieve_knowledge",
    "lookup_service_knowledge",
    "recall_experience",
    "get_current_time",
    "query_prometheus_alerts",
    "query_recent_changes",
]
