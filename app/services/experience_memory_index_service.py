"""Milvus index adapter for long-term diagnosis experience memory."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.vector_embedding_service import vector_embedding_service

MEMORY_TYPE = "diagnosis_experience"
VECTOR_DIM = 1024
ID_MAX_LENGTH = 100
TEXT_MAX_LENGTH = 8000


class ExperienceMemoryIndexService:
    """Search and maintain the Milvus experience memory collection."""

    def find_similar(self, *, query: str, project_id: str, top_k: int) -> list[dict[str, Any]]:
        try:
            query_vector = vector_embedding_service.embed_query(query)
            collection = self._collection()
            expr = (
                f'project_id == "{project_id}" '
                f'and enabled == true '
                f'and memory_type == "{MEMORY_TYPE}"'
            )
            results = collection.search(
                data=[query_vector],
                anns_field=config.rag_dense_vector_field,
                param={"metric_type": "L2", "params": {"nprobe": 10}},
                limit=top_k,
                expr=expr,
                output_fields=[
                    "experience_id",
                    "project_id",
                    "root_cause",
                    "resolution",
                    "confidence",
                    "enabled",
                ],
            )
        except Exception as exc:
            logger.warning(f"experience memory search failed: {exc}")
            return []

        candidates = []
        for hits in results:
            for hit in hits:
                candidates.append(
                    {
                        "experience_id": hit.entity.get("experience_id"),
                        "similarity": _distance_to_similarity(hit.distance),
                        "distance": hit.distance,
                    }
                )
        return candidates

    def upsert_memory(self, memory: dict[str, Any]) -> str:
        try:
            collection = self._collection()
            vector = vector_embedding_service.embed_query(memory["symptoms"])
            collection.upsert(
                [
                    [memory["experience_id"]],
                    [memory["experience_id"]],
                    [memory["project_id"]],
                    [memory["environment"]],
                    [memory["service_name"]],
                    [MEMORY_TYPE],
                    [memory["symptoms"]],
                    [memory["root_cause"]],
                    [memory["resolution"]],
                    [float(memory["confidence"])],
                    [bool(memory["enabled"])],
                    [_json_list(memory["source_case_ids"])],
                    [vector],
                ]
            )
            collection.flush()
        except Exception as exc:
            logger.warning(f"experience memory upsert failed: {exc}")
        return memory["experience_id"]

    def disable_memory(self, experience_id: str) -> None:
        try:
            collection = self._collection()
            collection.delete(expr=f'experience_id == "{experience_id}"')
            collection.flush()
        except Exception as exc:
            logger.warning(f"experience memory disable sync failed: {exc}")

    def rebuild(self, memories: list[dict[str, Any]]) -> int:
        count = 0
        for memory in memories:
            self.upsert_memory(memory)
            count += 1
        return count

    def _collection(self) -> Collection:
        milvus_manager.connect()
        collection_name = config.experience_memory_collection
        if not utility.has_collection(collection_name):
            collection = Collection(
                name=collection_name,
                schema=CollectionSchema(
                    fields=[
                        FieldSchema(
                            name="id",
                            dtype=DataType.VARCHAR,
                            max_length=ID_MAX_LENGTH,
                            is_primary=True,
                        ),
                        FieldSchema(
                            name="experience_id",
                            dtype=DataType.VARCHAR,
                            max_length=ID_MAX_LENGTH,
                        ),
                        FieldSchema(name="project_id", dtype=DataType.VARCHAR, max_length=100),
                        FieldSchema(name="environment", dtype=DataType.VARCHAR, max_length=100),
                        FieldSchema(name="service_name", dtype=DataType.VARCHAR, max_length=200),
                        FieldSchema(name="memory_type", dtype=DataType.VARCHAR, max_length=100),
                        FieldSchema(
                            name="symptoms",
                            dtype=DataType.VARCHAR,
                            max_length=TEXT_MAX_LENGTH,
                        ),
                        FieldSchema(
                            name="root_cause",
                            dtype=DataType.VARCHAR,
                            max_length=TEXT_MAX_LENGTH,
                        ),
                        FieldSchema(
                            name="resolution",
                            dtype=DataType.VARCHAR,
                            max_length=TEXT_MAX_LENGTH,
                        ),
                        FieldSchema(name="confidence", dtype=DataType.FLOAT),
                        FieldSchema(name="enabled", dtype=DataType.BOOL),
                        FieldSchema(
                            name="source_case_ids_json",
                            dtype=DataType.VARCHAR,
                            max_length=TEXT_MAX_LENGTH,
                        ),
                        FieldSchema(
                            name=config.rag_dense_vector_field,
                            dtype=DataType.FLOAT_VECTOR,
                            dim=VECTOR_DIM,
                        ),
                    ],
                    description="Long-term diagnosis experience memory",
                    enable_dynamic_field=False,
                ),
                num_shards=2,
            )
            collection.create_index(
                field_name=config.rag_dense_vector_field,
                index_params={
                    "metric_type": "L2",
                    "index_type": "IVF_FLAT",
                    "params": {"nlist": 128},
                },
            )

        collection = Collection(collection_name)
        collection.load()
        return collection


def _distance_to_similarity(distance: float) -> float:
    return 1.0 / (1.0 + max(float(distance), 0.0))


def _json_list(value: list[str]) -> str:
    return json.dumps(value, ensure_ascii=False)


experience_memory_index_service = ExperienceMemoryIndexService()
