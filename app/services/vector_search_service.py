"""向量检索服务模块"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pymilvus import AnnSearchRequest, Collection, WeightedRanker

from app.config import config
from app.core.milvus_client import milvus_manager


class SearchResult:
    """搜索结果类"""

    def __init__(
        self,
        id: str,
        content: str,
        score: float,
        source: str,
        metadata: dict[str, Any],
        retrieval_type: str,
        rank: int,
    ):
        self.id = id
        self.content = content
        self.score = score
        self.source = source
        self.metadata = metadata
        self.retrieval_type = retrieval_type
        self.rank = rank

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "content": self.content,
            "score": self.score,
            "source": self.source,
            "metadata": self.metadata,
            "retrieval_type": self.retrieval_type,
            "rank": self.rank,
        }


class VectorSearchService:
    """向量检索服务 - 负责从 Milvus 中搜索相似向量"""

    def __init__(self):
        """初始化向量检索服务"""
        logger.info("向量检索服务初始化完成")

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """统一知识库检索入口。

        根据配置在 dense、BM25 和 hybrid 检索模式之间分流。
        """

        mode = str(config.rag_retrieval_mode or "dense").strip().lower()
        if mode == "hybrid":
            return self.search_hybrid_documents(query=query, top_k=top_k)
        if mode == "bm25":
            return self.search_bm25_documents(query=query, top_k=top_k)
        if mode != "dense":
            logger.warning(f"未知 RAG 检索模式: {mode}，回退到 dense")
        return self.search_similar_documents(query=query, top_k=top_k)

    def search_similar_documents(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """
        搜索相似文档

        Args:
            query: 查询文本
            top_k: 返回最相似的K个结果

        Returns:
            List[SearchResult]: 搜索结果列表

        Raises:
            RuntimeError: 搜索失败时抛出
        """
        try:
            logger.info(f"开始搜索相似文档, 查询: {query}, topK: {top_k}")

            # 1. 将查询文本向量化
            query_vector = self._embed_query(query)
            logger.debug(f"查询向量生成成功, 维度: {len(query_vector)}")

            # 2. 获取 collection
            collection: Collection = milvus_manager.get_collection()

            # 3. 构建搜索参数
            search_params = {
                "metric_type": "L2",  # 欧氏距离
                "params": {"nprobe": 10},
            }

            # 4. 执行搜索
            results = collection.search(
                data=[query_vector],
                anns_field=config.rag_dense_vector_field,
                param=search_params,
                limit=top_k,
                output_fields=["id", "content", "metadata"],
            )

            # 5. 解析搜索结果
            search_results = self._parse_results(results, retrieval_type="dense")

            logger.info(f"搜索完成, 找到 {len(search_results)} 个相似文档")
            return search_results

        except Exception as e:
            logger.error(f"搜索相似文档失败: {e}")
            raise RuntimeError(f"搜索失败: {e}") from e

    def search_bm25_documents(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """使用 Milvus BM25 sparse vector 检索文档。"""

        try:
            logger.info(f"开始 BM25 搜索, 查询: {query}, topK: {top_k}")
            collection: Collection = milvus_manager.get_collection()
            results = collection.search(
                data=[query],
                anns_field=config.rag_sparse_vector_field,
                param=self._bm25_search_params(),
                limit=top_k,
                output_fields=["id", "content", "metadata"],
            )
            search_results = self._parse_results(results, retrieval_type="bm25")
            logger.info(f"BM25 搜索完成, 找到 {len(search_results)} 个文档")
            return search_results
        except Exception as e:
            logger.error(f"BM25 搜索失败: {e}")
            raise RuntimeError(f"BM25 搜索失败: {e}") from e

    def search_hybrid_documents(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """使用 dense vector + BM25 sparse vector 混合检索文档。"""

        try:
            logger.info(f"开始 hybrid 搜索, 查询: {query}, topK: {top_k}")
            query_vector = self._embed_query(query)
            collection: Collection = milvus_manager.get_collection()
            dense_request = AnnSearchRequest(
                data=[query_vector],
                anns_field=config.rag_dense_vector_field,
                param=self._dense_search_params(),
                limit=top_k,
            )
            sparse_request = AnnSearchRequest(
                data=[query],
                anns_field=config.rag_sparse_vector_field,
                param=self._bm25_search_params(),
                limit=top_k,
            )
            results = collection.hybrid_search(
                reqs=[dense_request, sparse_request],
                rerank=WeightedRanker(config.rag_dense_weight, config.rag_bm25_weight),
                limit=top_k,
                output_fields=["id", "content", "metadata"],
            )
            search_results = self._parse_results(results, retrieval_type="hybrid")
            logger.info(f"hybrid 搜索完成, 找到 {len(search_results)} 个文档")
            return search_results
        except Exception as e:
            logger.error(f"hybrid 搜索失败: {e}")
            raise RuntimeError(f"hybrid 搜索失败: {e}") from e

    def _embed_query(self, query: str) -> list[float]:
        from app.services.vector_embedding_service import vector_embedding_service

        return vector_embedding_service.embed_query(query)

    def _dense_search_params(self) -> dict[str, Any]:
        return {
            "metric_type": "L2",
            "params": {"nprobe": 10},
        }

    def _bm25_search_params(self) -> dict[str, Any]:
        return {
            "metric_type": "BM25",
            "params": {},
        }

    def _parse_results(self, results: Any, retrieval_type: str) -> list[SearchResult]:
        search_results = []
        for hits in results:
            for rank, hit in enumerate(hits, 1):
                metadata = hit.entity.get("metadata", {}) or {}
                source = (
                    metadata.get("_file_name")
                    or metadata.get("file_name")
                    or metadata.get("_source")
                    or metadata.get("source")
                    or "未知来源"
                )
                result = SearchResult(
                    id=hit.entity.get("id"),
                    content=hit.entity.get("content"),
                    score=hit.distance,
                    source=source,
                    metadata=metadata,
                    retrieval_type=retrieval_type,
                    rank=rank,
                )
                search_results.append(result)
        return search_results


# 全局单例
vector_search_service = VectorSearchService()
