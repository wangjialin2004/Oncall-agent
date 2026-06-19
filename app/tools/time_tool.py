"""Time tool."""

from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger
from pydantic import BaseModel, Field

from app.core.runtime_tools import make_runtime_tool


class CurrentTimeArgs(BaseModel):
    timezone: str = Field(default="Asia/Shanghai", description="IANA timezone name")


def _get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """Get the current time for a timezone."""

    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        return now.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.error(f"Time query tool failed: {e}")
        return f"Failed to get current time: {e}"


get_current_time = make_runtime_tool(
    name="get_current_time",
    description=_get_current_time.__doc__ or "",
    func=_get_current_time,
    args_schema=CurrentTimeArgs,
)
