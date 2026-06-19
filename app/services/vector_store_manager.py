"""Vector store manager backed directly by PyMilvus."""

import time
import uuid
from typing import Any

from loguru import logger
from pymilvus import Collection

from app.config import config
from app.core.milvus_client import milvus_manager
from app.models.document import RetrievedDocument
from app.services.vector_embedding_service import vector_embedding_service

COLLECTION_NAME = "biz"


class VectorStoreManager:
    """Manage vector writes and simple reads for the business knowledge collection."""

    def __init__(self):
        self.collection_name = COLLECTION_NAME

    def add_documents(self, documents: list[RetrievedDocument]) -> list[str]:
        """Embed and insert documents into Milvus."""
        if not documents:
            return []

        try:
            start_time = time.time()
            collection = self._get_collection()
            ids = [str(uuid.uuid4()) for _ in documents]
            contents = [doc.page_content for doc in documents]
            vectors = vector_embedding_service.embed_documents(contents)

            if len(vectors) != len(documents):
                raise RuntimeError(
                    f"Embedding count mismatch: documents={len(documents)}, embeddings={len(vectors)}"
                )

            rows = [
                {
                    "id": doc_id,
                    config.rag_dense_vector_field: vector,
                    "content": content,
                    "metadata": doc.metadata,
                }
                for doc_id, vector, content, doc in zip(
                    ids,
                    vectors,
                    contents,
                    documents,
                    strict=True,
                )
            ]

            collection.insert(rows)
            collection.flush()

            elapsed = time.time() - start_time
            logger.info(
                f"Added {len(documents)} documents to Milvus, elapsed={elapsed:.2f}s, "
                f"avg={elapsed / len(documents):.2f}s"
            )
            return ids
        except Exception as e:
            logger.error(f"Add documents failed: {e}")
            raise

    def delete_by_source(self, file_path: str) -> int:
        """Delete all chunks for a source file."""
        try:
            collection = self._get_collection()

            escaped_file_path = file_path.replace("\\", "\\\\").replace('"', '\\"')
            expr = f'metadata["_source"] == "{escaped_file_path}"'

            result = collection.delete(expr)
            deleted_count = result.delete_count if hasattr(result, "delete_count") else 0

            logger.info(f"Deleted old vectors for {file_path}, count={deleted_count}")
            return deleted_count

        except Exception as e:
            logger.warning(f"Delete old vectors failed, possibly first indexing run: {e}")
            return 0

    def get_vector_store(self) -> Collection:
        """Return the underlying Milvus collection."""
        return self._get_collection()

    def reinitialize(self) -> None:
        """Refresh the collection reference after collection rebuild."""
        _ = milvus_manager.connect()

    def similarity_search(self, query: str, k: int = 3) -> list[RetrievedDocument]:
        """Run a dense similarity search and return retrieved documents."""
        try:
            from app.services.vector_search_service import vector_search_service

            results = vector_search_service.search_similar_documents(query, top_k=k)
            docs: list[RetrievedDocument] = []
            for result in results:
                metadata: dict[str, Any] = {
                    "id": result.id,
                    "score": result.score,
                    "source": result.source,
                    "retrieval_type": result.retrieval_type,
                    "rank": result.rank,
                    **result.metadata,
                }
                docs.append(RetrievedDocument(page_content=result.content, metadata=metadata))
            return docs
        except Exception as e:
            logger.error(f"Similarity search failed: {e}")
            return []

    def _get_collection(self) -> Collection:
        try:
            return milvus_manager.get_collection()
        except RuntimeError:
            _ = milvus_manager.connect()
            return milvus_manager.get_collection()


vector_store_manager = VectorStoreManager()
