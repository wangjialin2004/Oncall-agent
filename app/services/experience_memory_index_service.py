"""Best-effort index adapter for long-term experience memory."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from loguru import logger
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.vector_embedding_service import vector_embedding_service

VECTOR_DIM = 1024
ID_MAX_LENGTH = 100
TEXT_MAX_LENGTH = 8000
DEFAULT_SHARD_NUMBER = 2


class ExperienceMemoryIndexService:
    def __init__(self, *, embedding_service: Any | None = None, milvus_client: Any | None = None):
        self.embedding_service = embedding_service or vector_embedding_service
        self.milvus_client = milvus_client or milvus_manager
        self._local_store: dict[str, Any] = {"records": []}
        self._milvus_collection: Collection | None = None

    def recall(
        self, *, query: str, project_id: str, top_k: int, session_id: str = ""
    ) -> list[dict[str, Any]]:
        try:
            query_vector = self._embed_query(query)
        except Exception as exc:
            logger.warning(f"experience recall unavailable: {exc}")
            return []

        try:
            results = self._recall_milvus(
                query_vector=query_vector,
                project_id=project_id,
                top_k=top_k,
            )
            if results:
                return results
        except Exception as exc:
            logger.warning(f"experience Milvus recall unavailable, using local cache: {exc}")
        return self._recall_local(query=query, project_id=project_id, top_k=top_k)

    def upsert(self, memory: dict[str, Any]) -> str:
        try:
            vector = self._embed_query(str(memory.get("symptoms") or ""))
        except Exception as exc:
            logger.warning(f"experience index upsert unavailable: {exc}")
            return str(memory.get("experience_id") or "")

        self._save_local_cache(memory)
        try:
            self._upsert_milvus(memory, vector)
        except Exception as exc:
            logger.warning(f"experience Milvus upsert unavailable, local cache retained: {exc}")
        return str(memory.get("experience_id") or "")

    def disable(self, experience_id: str) -> None:
        try:
            records = self._load_records()
            for record in records:
                if record.get("experience_id") == experience_id:
                    record["enabled"] = False
            self._save_records(records)
        except Exception as exc:
            logger.warning(f"experience index disable unavailable: {exc}")

    def rebuild(self, memories: list[dict[str, Any]]) -> int:
        self._save_records(memories)
        count = 0
        for memory in memories:
            try:
                vector = self._embed_query(str(memory.get("symptoms") or ""))
                self._upsert_milvus(memory, vector)
                count += 1
            except Exception as exc:
                logger.warning(f"experience Milvus rebuild item skipped: {exc}")
        return len(memories)

    def _embed_query(self, text: str) -> list[float]:
        if hasattr(self.embedding_service, "embed_query"):
            return self.embedding_service.embed_query(text)
        raise RuntimeError("embedding service does not provide embed_query")

    def _recall_milvus(
        self,
        *,
        query_vector: list[float],
        project_id: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        collection = self._collection()
        results = collection.search(
            data=[query_vector],
            anns_field=config.rag_dense_vector_field,
            param={"metric_type": "L2", "params": {"nprobe": 10}},
            limit=top_k,
            expr=f'project_id == "{_escape_expr_string(project_id)}" and enabled == true',
            output_fields=["experience_id"],
        )
        candidates = []
        for hits in results:
            for hit in hits:
                experience_id = _entity_get(hit, "experience_id") or _entity_get(hit, "id")
                if not experience_id:
                    continue
                candidates.append(
                    {
                        "experience_id": str(experience_id),
                        "similarity": _distance_to_similarity(float(getattr(hit, "distance", 0))),
                    }
                )
        return candidates

    def _upsert_milvus(self, memory: dict[str, Any], vector: list[float]) -> None:
        collection = self._collection()
        collection.upsert(
            [
                {
                    "id": str(memory.get("experience_id") or ""),
                    "experience_id": str(memory.get("experience_id") or ""),
                    "project_id": str(memory.get("project_id") or ""),
                    "environment": str(memory.get("environment") or ""),
                    "service_name": str(memory.get("service_name") or ""),
                    "symptoms": str(memory.get("symptoms") or "")[:TEXT_MAX_LENGTH],
                    "confidence": float(memory.get("confidence") or 0),
                    "enabled": bool(memory.get("enabled", True)),
                    config.rag_dense_vector_field: vector,
                }
            ]
        )
        collection.flush()

    def _collection(self) -> Collection:
        if self._milvus_collection is not None:
            return self._milvus_collection

        connect = getattr(self.milvus_client, "connect", None)
        if not callable(connect):
            raise RuntimeError("Milvus client does not provide connect")
        connect()

        collection_name = config.experience_memory_collection
        if not utility.has_collection(collection_name):
            collection = Collection(
                name=collection_name,
                schema=self._schema(),
                num_shards=DEFAULT_SHARD_NUMBER,
            )
            collection.create_index(
                field_name=config.rag_dense_vector_field,
                index_params={
                    "metric_type": "L2",
                    "index_type": "IVF_FLAT",
                    "params": {"nlist": 128},
                },
            )
        else:
            collection = Collection(collection_name)

        collection.load()
        self._milvus_collection = collection
        return collection

    def _schema(self) -> CollectionSchema:
        return CollectionSchema(
            fields=[
                FieldSchema(
                    name="id",
                    dtype=DataType.VARCHAR,
                    max_length=ID_MAX_LENGTH,
                    is_primary=True,
                ),
                FieldSchema(name="experience_id", dtype=DataType.VARCHAR, max_length=ID_MAX_LENGTH),
                FieldSchema(name="project_id", dtype=DataType.VARCHAR, max_length=ID_MAX_LENGTH),
                FieldSchema(name="environment", dtype=DataType.VARCHAR, max_length=ID_MAX_LENGTH),
                FieldSchema(name="service_name", dtype=DataType.VARCHAR, max_length=200),
                FieldSchema(name="symptoms", dtype=DataType.VARCHAR, max_length=TEXT_MAX_LENGTH),
                FieldSchema(name="confidence", dtype=DataType.FLOAT),
                FieldSchema(name="enabled", dtype=DataType.BOOL),
                FieldSchema(
                    name=config.rag_dense_vector_field,
                    dtype=DataType.FLOAT_VECTOR,
                    dim=VECTOR_DIM,
                ),
            ],
            description="Long-term diagnosis experience memory",
            enable_dynamic_field=False,
        )

    def _recall_local(self, *, query: str, project_id: str, top_k: int) -> list[dict[str, Any]]:
        records = self._load_records(project_id=project_id)
        query_text = _normalize(query)
        candidates = []
        for item in records:
            if item.get("enabled") is False:
                continue
            result = dict(item)
            result["similarity"] = _text_similarity(query_text, _normalize(str(item.get("symptoms") or "")))
            candidates.append(result)
        ranked = sorted(
            candidates,
            key=lambda item: (
                item.get("similarity", 0),
                item.get("confidence", 0),
                item.get("hit_count", 0),
            ),
            reverse=True,
        )
        return ranked[:top_k]

    def _load_records(self, project_id: str | None = None) -> list[dict[str, Any]]:
        store = self._store()
        records = list(store.get("records", []))
        if project_id:
            records = [item for item in records if item.get("project_id") == project_id]
        return records

    def _save_records(self, memories: list[dict[str, Any]]) -> None:
        self._write_store({"records": list(memories)})

    def _save_local_cache(self, memory: dict[str, Any]) -> None:
        records = self._load_records()
        records = [item for item in records if item.get("experience_id") != memory.get("experience_id")]
        records.append(dict(memory))
        self._save_records(records)

    def _store(self) -> dict[str, Any]:
        try:
            if hasattr(self.milvus_client, "_experience_memory_store"):
                return self.milvus_client._experience_memory_store  # type: ignore[attr-defined]
        except Exception:
            pass
        return self._local_store

    def _write_store(self, store: dict[str, Any]) -> None:
        self._local_store = store
        try:
            setattr(self.milvus_client, "_experience_memory_store", store)
        except Exception:
            pass


experience_memory_index_service = ExperienceMemoryIndexService()


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _distance_to_similarity(distance: float) -> float:
    return 1.0 / (1.0 + max(distance, 0.0))


def _escape_expr_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _entity_get(hit: Any, key: str) -> Any:
    entity = getattr(hit, "entity", None)
    if entity is not None and hasattr(entity, "get"):
        return entity.get(key)
    if hasattr(hit, "get"):
        return hit.get(key)
    return None
