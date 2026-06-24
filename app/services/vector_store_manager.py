"""Vector store manager backed directly by PyMilvus."""

import time
import uuid
from typing import Any

from loguru import logger
from pymilvus import Collection, FunctionType

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
        """Embed and insert documents into Milvus.

        每次插入同时写入 dense 向量和 content 字段。
        当 collection 以 bm25/hybrid 模式创建时，Milvus 内置的 BM25 Function
        会自动从 content 计算 sparse_vector，无需客户端显式传入稀疏向量。
        """
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

            # 检测 collection 是否具备 BM25 稀疏向量自动生成能力
            bm25_enabled = self._collection_has_bm25_function(collection)

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
            vector_info = (
                "dense + BM25 sparse (auto)" if bm25_enabled else "dense only"
            )
            logger.info(
                f"Added {len(documents)} documents to Milvus [{vector_info}], "
                f"elapsed={elapsed:.2f}s, avg={elapsed / len(documents):.2f}s"
            )
            return ids
        except Exception as e:
            logger.error(f"Add documents failed: {e}")
            raise

    @staticmethod
    def _is_bm25_function_type(func_type: Any) -> bool:
        if func_type is None:
            return False

        if getattr(func_type, "name", "").lower() == "bm25":
            return True

        func_type_text = str(func_type).lower()
        if func_type_text == "bm25" or func_type_text.endswith(".bm25"):
            return True

        try:
            return func_type == FunctionType.BM25 or int(func_type) == int(FunctionType.BM25)
        except Exception:
            return False

    @staticmethod
    def _collection_has_bm25_function(collection: Collection) -> bool:
        """检查 collection schema 中是否注册了 BM25 Function。

        当 BM25 Function 存在时，Milvus 会在 insert 时自动从 content 字段
        计算 SPARSE_FLOAT_VECTOR，无需客户端手动传入稀疏向量。
        """
        try:
            funcs = getattr(collection.schema, "functions", None) or []
            for func in funcs:
                func_type = (
                    getattr(func, "function_type", None)
                    or getattr(func, "type", None)
                )
                if VectorStoreManager._is_bm25_function_type(func_type):
                    return True
        except Exception:
            pass
        return False

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
