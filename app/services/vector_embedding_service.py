"""Vector embedding service."""

from loguru import logger
from openai import OpenAI

from app.config import config


class DashScopeEmbeddings:
    """DashScope text embedding client."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
    ):
        if not api_key or api_key == "your-api-key-here":
            raise ValueError("请设置环境变量 DASHSCOPE_API_KEY")

        self.client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.model = model
        self.dimensions = dimensions

        masked_key = self._mask_api_key(api_key)
        logger.info(
            f"DashScope Embeddings initialized - model: {model}, dims: {dimensions}, api_key: {masked_key}"
        )

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        if len(api_key) > 8:
            return f"{api_key[:8]}...{api_key[-4:]}"
        return "***"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            logger.info(f"Embedding {len(texts)} documents")
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
                dimensions=self.dimensions,
                encoding_format="float",
            )
            embeddings = [item.embedding for item in response.data]
            logger.debug(f"Embedded batch complete, dims={len(embeddings[0]) if embeddings else 0}")
            return embeddings
        except Exception as e:
            logger.error(f"Document embedding failed: {e}")
            raise RuntimeError(f"Document embedding failed: {e}") from e

    def embed_query(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("查询文本不能为空")

        try:
            logger.debug(f"Embedding query, length={len(text)}")
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimensions,
                encoding_format="float",
            )
            embedding = response.data[0].embedding
            logger.debug(f"Query embedding complete, dims={len(embedding)}")
            return embedding
        except Exception as e:
            logger.error(f"Query embedding failed: {e}")
            raise RuntimeError(f"Query embedding failed: {e}") from e


vector_embedding_service = DashScopeEmbeddings(
    api_key=config.dashscope_api_key,
    model=config.dashscope_embedding_model,
    dimensions=1024,
)
