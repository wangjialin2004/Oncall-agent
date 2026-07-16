"""Change / release query tool (interface placeholder).

M2 W7 formal decision (option B): change data source remains **unavailable**
until a separate epic wires a real read-only CI/CD / CMDB / ticket source.
``CHANGE_SOURCE_AVAILABLE`` stays False; ``CHANGE_SOURCE_POLICY=unavailable``.
"""

import json

from loguru import logger
from pydantic import BaseModel, Field

from app.core.runtime_tools import make_runtime_tool

# Hard false until a real change/release data source is connected (option B).
# Do not flip this without implementing ``_query_recent_changes`` live path and
# updating docs/pilot/change-capability-unavailable.md.
CHANGE_SOURCE_AVAILABLE = False


def change_source_policy() -> str:
    """Return configured policy: unavailable | future (never pretend available)."""
    try:
        from app.config import config

        raw = str(getattr(config, "change_source_policy", "unavailable") or "unavailable")
    except Exception:
        raw = "unavailable"
    policy = raw.strip().lower()
    if policy not in {"unavailable", "future"}:
        return "unavailable"
    return policy


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

    policy = change_source_policy()
    if not CHANGE_SOURCE_AVAILABLE or policy in {"unavailable", "future"}:
        return json.dumps(
            {
                "success": False,
                "source_available": False,
                "capability": "change_query",
                "policy": policy,
                "service": service,
                "time_window": time_window,
                "limit": limit,
                "changes": [],
                "gap": "missing_change_datasource",
                "message": (
                    "当前未接入变更/发布数据源（CI-CD / CMDB / 工单系统），"
                    "产品策略为永久降权直至独立 epic 接入只读源（M2 W7 option B）。"
                    "请明确声明：缺少变更证据，不要编造发布版本号、操作人或发布时间；"
                    "可回退知识库给出一般性关联排查建议，并提示用户到发布系统核实。"
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
