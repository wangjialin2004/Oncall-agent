from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.config import config
from app.models.memory import (
    ExperienceMemoryUpdateRequest,
    ManualExperienceCreateRequest,
    MemoryFeedbackRequest,
    ServiceBaselineRequest,
    ServiceUpsertRequest,
    UserPreferenceRequest,
)
from app.services.experience_memory_service import experience_memory_service
from app.services.service_knowledge_service import service_knowledge_service
from app.services.session_scope_service import require_session_owner
from app.services.user_preference_service import user_preference_service

__all__ = [
    "router",
    "experience_memory_service",
    "service_knowledge_service",
    "user_preference_service",
]

router = APIRouter()


@router.post("/memory/feedback")
async def create_memory_from_feedback(
    request: MemoryFeedbackRequest,
    owner_key: str = Depends(require_session_owner),
):
    try:
        if request.acceptance_level == "weak":
            experience_id = experience_memory_service.create_weak_acceptance(
                project_id=config.project_id,
                session_id=request.session_id,
                user_message=request.user_message,
                assistant_answer=request.assistant_answer,
                environment=request.environment,
                service_name=request.service_name,
                events=request.events,
            )
        else:
            experience_id = experience_memory_service.create_from_feedback(
                project_id=config.project_id,
                session_id=request.session_id,
                user_message=request.user_message,
                assistant_answer=request.assistant_answer,
                user_accepted=request.user_accepted,
                actual_root_cause=request.actual_root_cause,
                final_resolution=request.final_resolution,
                environment=request.environment,
                service_name=request.service_name,
                events=request.events,
                source_feedback_id=f"feedback:{owner_key}:{request.session_id}",
            )
        return {"code": 200, "message": "success", "data": {"experience_id": experience_id}}
    except Exception as exc:
        logger.error(f"memory feedback failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/memory/experiences")
async def create_manual_experience(request: ManualExperienceCreateRequest):
    try:
        experience_id = experience_memory_service.create_manual(
            project_id=config.project_id,
            symptoms=request.symptoms,
            root_cause=request.root_cause,
            resolution=request.resolution,
            evidence_summary=request.evidence_summary,
            environment=request.environment,
            service_name=request.service_name,
            confidence=request.confidence,
        )
        return {"code": 200, "message": "success", "data": {"experience_id": experience_id}}
    except Exception as exc:
        logger.error(f"manual memory create failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/memory/experiences")
async def list_experiences(
    project_id: str | None = None,
    enabled: bool | None = None,
    limit: int = 50,
):
    memories = experience_memory_service.list(
        project_id=project_id or config.project_id,
        enabled=enabled,
        limit=limit,
    )
    return {"code": 200, "message": "success", "data": memories}


@router.get("/memory/experiences/{experience_id}")
async def get_experience(experience_id: str):
    memory = experience_memory_service.get(experience_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="experience memory not found")
    return {"code": 200, "message": "success", "data": memory}


@router.patch("/memory/experiences/{experience_id}")
async def update_experience(experience_id: str, request: ExperienceMemoryUpdateRequest):
    updated = experience_memory_service.update(
        experience_id,
        enabled=request.enabled,
        confidence=request.confidence,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="experience memory not found")
    return {
        "code": 200,
        "message": "success",
        "data": {"experience_id": experience_id, "updated": True},
    }


@router.post("/memory/experiences/rebuild-index")
async def rebuild_index(project_id: str | None = None):
    indexed = experience_memory_service.rebuild_index(project_id=project_id or config.project_id)
    return {"code": 200, "message": "success", "data": {"indexed": indexed}}


@router.get("/memory/services")
async def list_services(environment: str | None = None):
    services = service_knowledge_service.list_services(
        project_id=config.project_id,
        environment=environment,
    )
    return {"code": 200, "message": "success", "data": services}


@router.get("/memory/services/{service_name}")
async def get_service_knowledge(service_name: str, environment: str = "prod"):
    service = service_knowledge_service.lookup(
        project_id=config.project_id,
        service_name=service_name,
        environment=environment,
    )
    if service is None:
        raise HTTPException(status_code=404, detail="service knowledge not found")
    return {"code": 200, "message": "success", "data": service}


@router.put("/memory/services/{service_name}")
async def upsert_service(service_name: str, request: ServiceUpsertRequest):
    service_knowledge_service.upsert_service(
        project_id=config.project_id,
        service_name=service_name,
        environment=request.environment,
        owner_team=request.owner_team,
        owner_user=request.owner_user,
        description=request.description,
        enabled=request.enabled,
    )
    return {"code": 200, "message": "success", "data": {"service_name": service_name}}


@router.put("/memory/services/{service_name}/baselines")
async def upsert_service_baseline(service_name: str, request: ServiceBaselineRequest):
    service_knowledge_service.upsert_baseline(
        project_id=config.project_id,
        service_name=service_name,
        environment=request.environment,
        metric_name=request.metric_name,
        min_value=request.min_value,
        max_value=request.max_value,
        unit=request.unit,
        sample_window=request.sample_window,
    )
    return {"code": 200, "message": "success", "data": {"service_name": service_name}}


@router.delete("/memory/services/{service_name}/baselines/{metric_name}")
async def delete_service_baseline(
    service_name: str,
    metric_name: str,
    environment: str = "prod",
):
    deleted = service_knowledge_service.delete_baseline(
        project_id=config.project_id,
        service_name=service_name,
        environment=environment,
        metric_name=metric_name,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="baseline not found")
    return {"code": 200, "message": "success", "data": {"service_name": service_name, "metric_name": metric_name}}


@router.post("/memory/services/import-seed")
async def import_service_seed():
    imported = await service_knowledge_service.import_from_monitor_mcp(project_id=config.project_id)
    return {"code": 200, "message": "success", "data": {"imported": imported}}


@router.get("/memory/preferences")
async def get_preferences(owner_key: str = Depends(require_session_owner)):
    preference = user_preference_service.get(owner_key)
    return {"code": 200, "message": "success", "data": preference or {}}


@router.put("/memory/preferences")
async def update_preferences(
    request: UserPreferenceRequest,
    owner_key: str = Depends(require_session_owner),
):
    preference = user_preference_service.upsert(
        owner_key=owner_key,
        default_environment=request.default_environment,
        language=request.language,
        detail_level=request.detail_level,
        focused_services=request.focused_services,
        notes=request.notes,
    )
    return {"code": 200, "message": "success", "data": preference}
