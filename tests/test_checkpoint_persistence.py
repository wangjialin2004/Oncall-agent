import pytest

from app.services.aiops_service import AIOpsService
from app.services.checkpoint_service import aclose_checkpointer
from app.services.rag_agent_service import RagAgentService


@pytest.mark.asyncio
async def test_rag_agent_service_accepts_sqlite_checkpoint_path(tmp_path):
    db_path = tmp_path / "rag-checkpoints.db"

    service = RagAgentService(streaming=False, checkpoint_db_path=db_path)
    await service._initialize_agent()

    try:
        assert service.checkpointer.__class__.__name__ == "AsyncSqliteSaver"
        assert hasattr(service.checkpointer, "aget_tuple")
        assert db_path.parent.exists()
    finally:
        await aclose_checkpointer(service.checkpointer)


@pytest.mark.asyncio
async def test_aiops_service_accepts_sqlite_checkpoint_path(tmp_path):
    db_path = tmp_path / "aiops-checkpoints.db"

    service = AIOpsService(checkpoint_db_path=db_path)
    await service._initialize_graph()

    try:
        assert service.checkpointer.__class__.__name__ == "AsyncSqliteSaver"
        assert hasattr(service.checkpointer, "aget_tuple")
        assert db_path.parent.exists()
    finally:
        await aclose_checkpointer(service.checkpointer)
