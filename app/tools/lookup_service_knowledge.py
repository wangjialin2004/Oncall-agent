from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import config
from app.core.runtime_tools import make_runtime_tool
from app.services.service_knowledge_service import service_knowledge_service


class LookupServiceKnowledgeArgs(BaseModel):
    service_name: str = Field(description="Service name")
    environment: str = Field(default="prod", description="Environment")


def _lookup_service_knowledge(service_name: str, environment: str = "prod") -> str:
    service = service_knowledge_service.lookup(
        project_id=config.project_id,
        service_name=service_name,
        environment=environment,
    )
    if not service:
        return "未找到服务知识。"

    lines = [
        f"服务: {service['service_name']}",
        f"环境: {service['environment']}",
        f"归属: {service['owner_team'] or service['owner_user'] or 'unknown'}",
    ]
    if service.get("description"):
        lines.append(f"描述: {service['description']}")
    for baseline in service.get("baselines", []):
        lines.append(
            "基线: "
            f"{baseline['metric_name']} {baseline['min_value']}-{baseline['max_value']} {baseline['unit']}".strip()
        )
    for relation in service.get("relations", []):
        lines.append(f"关系: {relation['relation_type']} -> {relation['target_service']}")
    return "\n".join(lines)


lookup_service_knowledge = make_runtime_tool(
    name="lookup_service_knowledge",
    description="Lookup structured service knowledge and baselines for a service.",
    func=_lookup_service_knowledge,
    args_schema=LookupServiceKnowledgeArgs,
)
