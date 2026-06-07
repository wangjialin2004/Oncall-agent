"""本地日志 MCP Server.

保留 CLS 风格工具名，同时把数据源切换为本项目 ``logs/`` 目录。
"""

from __future__ import annotations

import functools
import json
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("CLS_MCP_Server")

mcp = FastMCP("CLS")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_LINE_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*(?:\||\s)\s*"
    r"(?P<level>TRACE|DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)?\s*(?:\||\s)?\s*(?P<message>.*)$",
    re.IGNORECASE,
)


def log_tool_call(func):
    """记录工具调用的日志，包括方法名、参数和返回状态。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        method_name = func.__name__
        logger.info("=" * 80)
        logger.info("调用方法: %s", method_name)
        if kwargs:
            try:
                logger.info("参数信息:\n%s", json.dumps(kwargs, ensure_ascii=False, indent=2))
            except (TypeError, ValueError):
                logger.info("参数信息: %s", kwargs)
        else:
            logger.info("参数信息: 无")

        try:
            result = func(*args, **kwargs)
            logger.info("返回状态: SUCCESS")
            if isinstance(result, dict):
                summary = {
                    k: v if not isinstance(v, (list, dict)) else f"<{type(v).__name__} with {len(v)} items>"
                    for k, v in list(result.items())[:5]
                }
                logger.info("返回结果摘要: %s", json.dumps(summary, ensure_ascii=False))
            else:
                logger.info("返回结果: %s", result)
            logger.info("=" * 80)
            return result
        except Exception as e:
            logger.error("返回状态: ERROR")
            logger.error("错误信息: %s", e)
            logger.error("=" * 80)
            raise

    return wrapper


def _evidence(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "success",
        "source": "local_logs",
        "evidence_id": f"cls-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
        **payload,
    }


def _timed_evidence(start_time: float, payload: dict[str, Any]) -> dict[str, Any]:
    return _evidence(
        {
            "duration_ms": round((time.perf_counter() - start_time) * 1000, 2),
            **payload,
        }
    )


def _timestamp_to_datetime(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000)


class LocalLogProvider:
    """读取本项目本地日志文件。"""

    def __init__(self, logs_dir: Path = PROJECT_ROOT / "logs"):
        self.logs_dir = logs_dir

    def list_files(self) -> list[dict[str, Any]]:
        if not self.logs_dir.exists():
            return []
        files = []
        for path in sorted(self.logs_dir.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True):
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        return files

    def read_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for item in self.list_files():
            path = Path(item["path"])
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, 1):
                parsed = self._parse_line(line)
                parsed["source"] = str(path)
                parsed["line_no"] = line_no
                entries.append(parsed)
        entries.sort(key=lambda entry: entry.get("timestamp", ""), reverse=True)
        return entries[: max(0, limit)]

    def search(
        self,
        keyword: str | None = None,
        level: str | None = None,
        limit: int = 100,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        matched = []
        normalized_level = level.upper() if level else None
        for entry in self.read_entries(limit=10_000):
            entry_level = str(entry.get("level") or "").upper()
            message = str(entry.get("message") or "")
            timestamp_text = str(entry.get("timestamp") or "")
            entry_dt = self._parse_timestamp(timestamp_text)

            if normalized_level and entry_level != normalized_level:
                continue
            if keyword and keyword.lower() not in message.lower() and keyword.lower() not in entry_level.lower():
                continue
            if start_time and entry_dt and entry_dt < start_time:
                continue
            if end_time and entry_dt and entry_dt > end_time:
                continue

            matched.append(entry)
            if len(matched) >= limit:
                break
        return matched

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    @staticmethod
    def _parse_line(line: str) -> dict[str, Any]:
        match = LOG_LINE_PATTERN.match(line.strip())
        if not match:
            return {"timestamp": "", "level": "UNKNOWN", "message": line.strip(), "raw": line}

        level = (match.group("level") or "INFO").upper()
        if level == "WARN":
            level = "WARNING"
        message = (match.group("message") or "").strip()
        if " | " in message:
            message = message.split(" | ", maxsplit=2)[-1].strip()
        return {
            "timestamp": match.group("timestamp"),
            "level": level,
            "message": message,
            "raw": line,
        }


log_provider = LocalLogProvider()


@mcp.tool()
@log_tool_call
def get_current_timestamp() -> int:
    """获取当前时间戳（毫秒）。"""

    return int(datetime.now().timestamp() * 1000)


@mcp.tool()
@log_tool_call
def get_region_code_by_name(region_name: str) -> dict[str, Any]:
    """兼容旧 CLS 工具：根据地区名称返回本地地区标识。"""

    mapping = {
        "本地": {"region_code": "local", "region_name": "本地", "available": True},
        "北京": {"region_code": "ap-beijing", "region_name": "北京", "available": True},
        "上海": {"region_code": "ap-shanghai", "region_name": "上海", "available": True},
        "广州": {"region_code": "ap-guangzhou", "region_name": "广州", "available": True},
    }
    return mapping.get(
        region_name,
        {
            "region_code": None,
            "region_name": region_name,
            "available": False,
            "error": f"未找到地区: {region_name}",
        },
    )


@mcp.tool()
@log_tool_call
def get_topic_info_by_name(topic_name: str, region_code: str | None = None) -> dict[str, Any]:
    """根据主题名称返回本地应用日志 topic 信息。"""

    topic = {
        "topic_id": "local-app-logs",
        "topic_name": "本地应用日志",
        "service_name": "aiops-assistant-api",
        "region_code": region_code or "local",
        "create_time": "",
        "log_count": len(log_provider.read_entries(limit=10_000)),
        "description": "本项目 logs/ 目录下的应用日志",
    }
    if topic_name in {topic["topic_name"], "应用日志", "本地日志", "数据同步服务日志"}:
        return topic
    return {
        "topic_id": None,
        "topic_name": topic_name,
        "region_code": region_code,
        "error": f"未找到主题: {topic_name}",
    }


@mcp.tool()
@log_tool_call
def search_topic_by_service_name(
    service_name: str,
    region_code: str | None = None,
    fuzzy: bool = True,
) -> dict[str, Any]:
    """根据服务名称搜索本地日志 topic。"""

    topic = {
        "topic_id": "local-app-logs",
        "topic_name": "本地应用日志",
        "service_name": "aiops-assistant-api",
        "region_code": region_code or "local",
        "create_time": "",
        "log_count": len(log_provider.read_entries(limit=10_000)),
        "description": "本项目 logs/ 目录下的应用日志",
    }
    target = topic["service_name"].lower()
    query = service_name.lower()
    matched = query == target or (fuzzy and (query in target or target in query or "app" in query))
    topics = [topic] if matched else []
    return {
        "total": len(topics),
        "topics": topics,
        "query": {"service_name": service_name, "region_code": region_code, "fuzzy": fuzzy},
        "message": f"找到 {len(topics)} 个匹配的日志主题" if topics else f"未找到服务 '{service_name}' 的日志主题",
    }


@mcp.tool()
@log_tool_call
def get_log_files() -> dict[str, Any]:
    """列出项目本地日志文件。"""

    start = time.perf_counter()
    files = log_provider.list_files()
    return _timed_evidence(start, {"files": files, "total": len(files), "logs_dir": str(log_provider.logs_dir)})


@mcp.tool()
@log_tool_call
def get_recent_app_logs(limit: int = 50) -> dict[str, Any]:
    """读取最近应用日志。"""

    start = time.perf_counter()
    logs = log_provider.read_entries(limit=limit)
    return _timed_evidence(start, {"logs": logs, "total": len(logs), "limit": limit})


@mcp.tool()
@log_tool_call
def search_app_logs(
    keyword: str | None = None,
    level: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """按关键词和日志级别搜索项目本地日志。"""

    start = time.perf_counter()
    logs = log_provider.search(keyword=keyword, level=level, limit=limit)
    return _timed_evidence(
        start,
        {
            "logs": logs,
            "total": len(logs),
            "query": {"keyword": keyword, "level": level, "limit": limit},
        },
    )


@mcp.tool()
@log_tool_call
def search_log(
    topic_id: str,
    start_time: int,
    end_time: int,
    query: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """兼容旧 CLS 工具：基于本地日志文件搜索。"""

    start = time.perf_counter()
    if topic_id not in {"local-app-logs", "topic-001"}:
        return _timed_evidence(
            start,
            {
                "topic_id": topic_id,
                "start_time": start_time,
                "end_time": end_time,
                "query": query,
                "limit": limit,
                "total": 0,
                "logs": [],
                "error": f"主题不存在: {topic_id}",
                "message": f"错误: 未找到主题 {topic_id}，请检查 topic_id 是否正确",
            },
        )

    logs = log_provider.search(
        keyword=query,
        limit=limit,
        start_time=_timestamp_to_datetime(start_time),
        end_time=_timestamp_to_datetime(end_time),
    )
    return _timed_evidence(
        start,
        {
            "topic_id": topic_id,
            "start_time": start_time,
            "end_time": end_time,
            "query": query,
            "limit": limit,
            "total": len(logs),
            "logs": logs,
            "message": f"成功查询 {len(logs)} 条本地应用日志",
        },
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8003, path="/mcp")
