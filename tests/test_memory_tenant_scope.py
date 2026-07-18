from pathlib import Path

from app.services.experience_memory_service import ExperienceMemoryService
from app.services.memory_cache import reset_default_cache


def test_user_draft_is_visible_only_to_owner(tmp_path: Path):
    reset_default_cache()
    service = ExperienceMemoryService(tmp_path / "tenant-memory.db")
    experience_id = service.create_draft_from_run(
        project_id="p1",
        session_id="s1",
        user_message="cpu high",
        assistant_answer="check workers",
        owner_key="owner-a",
    )

    owner_a = service.list_pending(project_id="p1", owner_key="owner-a")
    owner_b = service.list_pending(project_id="p1", owner_key="owner-b")

    assert [item["experience_id"] for item in owner_a] == [experience_id]
    assert owner_b == []
    assert (
        service.confirm_draft(
            experience_id,
            owner_key="owner-b",
            project_id="p1",
        )
        is None
    )


def test_owner_confirm_is_atomic_and_promotes_visibility(tmp_path: Path):
    reset_default_cache()
    service = ExperienceMemoryService(tmp_path / "tenant-memory.db")
    experience_id = service.create_draft_from_run(
        project_id="p1",
        session_id="s1",
        user_message="disk high",
        assistant_answer="check retention",
        owner_key="owner-a",
    )

    confirmed = service.confirm_draft(
        experience_id,
        owner_key="owner-a",
        project_id="p1",
        approved_by="curator-a",
    )

    assert confirmed is not None
    assert confirmed["status"] == "active"
    assert confirmed["visibility"] == "project"
    assert confirmed["approved_by"] == "curator-a"
    assert (
        service.confirm_draft(
            experience_id,
            owner_key="owner-a",
            project_id="p1",
        )
        == confirmed
    )
