"""配置管理模块

使用 Pydantic Settings 实现类型安全的配置管理
"""

from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用配置
    app_name: str = "智能运维助手"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 9900

    # DashScope 配置
    dashscope_api_key: str = ""  # 默认空字符串，实际使用需从环境变量加载
    dashscope_model: str = "qwen-max"
    dashscope_embedding_model: str = "text-embedding-v4"  # v4 支持多种维度（默认 1024）

    # Generic LLM provider configuration. When unset, the custom LLM client
    # falls back to the legacy DashScope settings above.
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: float = 60.0

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # RAG 配置
    rag_top_k: int = 3
    rag_model: str = "qwen-max"  # 使用快速响应模型，不带扩展思考
    rag_retrieval_mode: str = "dense"  # dense | bm25 | hybrid
    rag_dense_weight: float = 0.7
    rag_bm25_weight: float = 0.3
    rag_dense_vector_field: str = "vector"
    rag_sparse_vector_field: str = "sparse_vector"

    # 文档分块配置
    chunk_max_size: int = 800
    chunk_overlap: int = 100

    # MCP 服务配置（transport: stdio | sse | streamable-http）
    # 腾讯云托管 MCP 的 URL 通常含 /sse/，需使用 sse；本地 FastMCP 使用 streamable-http
    mcp_cls_transport: str = "streamable-http"
    mcp_cls_url: str = "http://localhost:8003/mcp"
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"

    # Prometheus
    prometheus_base_url: str = "http://127.0.0.1:9090"
    prometheus_request_timeout: float = 10.0

    # Provider modes
    monitor_target_mode: str = "self"
    log_provider: str = "local"

    # Diagnosis memory
    diagnosis_memory_db_path: str = "data/diagnosis_memory.sqlite3"
    checkpoint_db_path: str = "volumes/checkpoints.db"

    # Long-term experience memory
    project_id: str = "super_biz_agent"
    experience_memory_db_path: str = "data/experience_memory.sqlite3"
    experience_memory_collection: str = "experience_memory"
    experience_memory_top_k: int = 3
    experience_memory_similarity_threshold: float = 0.78
    experience_memory_high_confidence_threshold: float = 0.75
    experience_memory_initial_confidence: float = 0.8

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug_mode(cls, value: Any) -> Any:
        """兼容 DEBUG=release/prod 这类部署环境值。"""

        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "prod", "production"}:
                return False
            if normalized in {"debug", "dev", "development"}:
                return True
        return value

    @property
    def mcp_servers(self) -> dict[str, dict[str, Any]]:
        """获取完整的 MCP 服务器配置"""
        return {
            "cls": {
                "transport": self.mcp_cls_transport,
                "url": self.mcp_cls_url,
            },
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            }
        }


# 全局配置实例
config = Settings()
