"""Change / release query tool (interface placeholder).

There is currently no change-management / CI-CD / CMDB data source wired into the
project. This tool defines the stable interface the 变更/发布 expert calls, and
returns a structured "no data source" payload so the expert can fall back to the
knowledge base. When a real source (e.g. a CI-CD or CMDB MCP server) is available,
replace ``_query_recent_changes`` with the live integration — the schema below is
designed to match a typical change-record API.
"""

import json

from loguru import logger
from pydantic import BaseModel, Field

from app.core.runtime_tools import make_runtime_tool

# Set to True once a real change/release data source is connected.
CHANGE_SOURCE_AVAILABLE = False


class QueryRecentChangesArgs(BaseModel):
    service: str = Field(
        default="",
        description="受影响的服务名；留空表示查询全部服务的近期变更",
    )
    time_window: str = Field(
        default="24h",
        description="时间窗口，如 '1h'、'24h'、'7d'",
    )
    limit: int = Field(default=20, description="返回的最大变更记录数")


def _query_recent_changes(service: str = "", time_window: str = "24h", limit: int = 20) -> str:
    """Query recent change/release/deploy records for a service.

    Returns deployments, config changes, rollbacks and tickets within the time
    window. Use this to correlate incidents with recent changes.
    """
    logger.info(
        f"query_recent_changes called: service={service!r}, "
        f"time_window={time_window!r}, limit={limit}"
    )

    if not CHANGE_SOURCE_AVAILABLE:
        return json.dumps(
            {
                "success": False,
                "source_available": False,
                "service": service,
                "time_window": time_window,
                "changes": [],
                "message": (
                    "暂未接入变更/发布数据源（CI-CD / CMDB / 工单系统）。"
                    "请基于知识库与运维经验回答，并提示用户该结论缺少变更数据支撑。"
                ),
            },
            ensure_ascii=False,
        )

    # 真实数据源接入点：返回结构应为
    # {"success": True, "source_available": True, "changes": [
    #     {"change_id", "type": "deploy|config|rollback|ticket", "service",
    #      "version", "operator", "started_at", "finished_at", "status", "summary"}
    # ]}
    raise NotImplementedError("Live change source not implemented yet")


query_recent_changes = make_runtime_tool(
    name="query_recent_changes",
    description=_query_recent_changes.__doc__ or "",
    func=_query_recent_changes,
    args_schema=QueryRecentChangesArgs,
)
