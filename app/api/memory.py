from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger

from app.models.memory import ExperienceMemoryUpdateRequest
from app.services.experience_memory_service import experience_memory_service

router = APIRouter()


@router.get("/memory/experiences")
async def list_experience_memories(
    project_id: str | None = None,
    enabled: bool | None = None,
    service_name: str | None = None,
    min_confidence: float | None = None,
    limit: int = 50,
    offset: int = 0,
):
    try:
        memories = experience_memory_service.list_memories(
            project_id=project_id,
            enabled=enabled,
            service_name=service_name,
            min_confidence=min_confidence,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.error(f"list experience memories failed: {exc}")
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "error", "data": None},
        )
    return {"code": 200, "message": "success", "data": memories}


@router.get("/memory/experiences/{experience_id}")
async def get_experience_memory(experience_id: str):
    try:
        memory = experience_memory_service.get_memory(experience_id)
    except Exception as exc:
        logger.error(f"get experience memory failed: {exc}")
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "error", "data": None},
        )
    if memory is None:
        return JSONResponse(
            status_code=404,
            content={
                "code": 404,
                "message": f"Experience memory not found: {experience_id}",
                "data": None,
            },
        )
    return {"code": 200, "message": "success", "data": memory}


@router.patch("/memory/experiences/{experience_id}")
async def update_experience_memory(
    experience_id: str,
    request: ExperienceMemoryUpdateRequest,
):
    try:
        changed = experience_memory_service.set_enabled(
            experience_id,
            enabled=request.enabled,
        )
    except Exception as exc:
        logger.error(f"update experience memory failed: {exc}")
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "error", "data": None},
        )
    if not changed:
        return JSONResponse(
            status_code=404,
            content={
                "code": 404,
                "message": f"Experience memory not found: {experience_id}",
                "data": None,
            },
        )
    return {
        "code": 200,
        "message": "success",
        "data": {"experience_id": experience_id, "enabled": request.enabled},
    }


@router.post("/memory/experiences/rebuild-index")
async def rebuild_experience_memory_index(project_id: str | None = None):
    try:
        indexed = experience_memory_service.rebuild_index(project_id=project_id)
    except Exception as exc:
        logger.error(f"rebuild experience memory index failed: {exc}")
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "error", "data": None},
        )
    return {"code": 200, "message": "success", "data": {"indexed": indexed}}
