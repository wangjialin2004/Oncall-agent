"""Time tool."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger
from pydantic import BaseModel, Field

from app.core.runtime_tools import make_runtime_tool


class CurrentTimeArgs(BaseModel):
    timezone: str = Field(default="Asia/Shanghai", description="IANA timezone name")


def _get_current_time(timezone: str = "Asia/Shanghai") -> str | dict[str, Any]:
    """Get the current time for a timezone."""

    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        return now.strftime("%Y-%m-%d %H:%M:%S")
    except ZoneInfoNotFoundError as e:
        logger.error(f"Time query tool failed: {e}")
        return {
            "success": False,
            "status": "error",
            "error_code": "invalid_timezone",
            "retryable": False,
            "message": "指定时区无效或不可用。",
        }
    except Exception as e:
        logger.error(f"Time query tool failed: {e}")
        return {
            "success": False,
            "status": "error",
            "error_code": "time_lookup_failed",
            "retryable": True,
            "message": "时间服务暂时不可用。",
        }


get_current_time = make_runtime_tool(
    name="get_current_time",
    description=_get_current_time.__doc__ or "",
    func=_get_current_time,
    args_schema=CurrentTimeArgs,
)
