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
    dashscope_model: str = ""
    dashscope_embedding_model: str = "text-embedding-v4"  # v4 支持多种维度（默认 1024）

    # Generic LLM provider configuration. When unset, the custom LLM client
    # falls back to the legacy DashScope settings above.
    llm_provider: str = "openai"  # openai | azure | custom
    llm_base_url: str = "https://dasuapi.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-5.4"
    llm_timeout: float = 60.0
    # 瞬时错误（429 / 5xx / 网络超时）的指数退避重试次数；鉴权错误不重试
    llm_max_retries: int = 2
    llm_retry_base_delay: float = 0.5

    # Router + 专家 Agent 配置
    # 语义路由低于该置信度时回退到综合诊断（最稳，可跨域排查）
    router_min_confidence: float = 0.55
    # 将关键词分为强/弱两层；弱词只作为语义路由提示，避免单个泛化词误导路由
    router_keyword_tiering_enabled: bool = True
    # 单个专家执行超时（秒），超时返回降级答案
    expert_timeout_seconds: float = 60.0

    # Harness 主循环配置（默认关闭，旧 RouterService 路径保留可回滚）
    harness_enabled: bool = False
    harness_max_steps: int = 6
    harness_token_budget: int = 16000
    harness_history_max_turns: int = 6
    harness_timeout_seconds: float = 90.0
    harness_mcp_enabled: bool = False
    harness_delegation_enabled: bool = True
    harness_tool_timeout_seconds: float = 30.0
    harness_tool_max_output_chars: int = 6000
    # 工具瞬时错误（超时/网络/5xx）的有限重试次数；鉴权/权限类错误不重试
    harness_tool_max_retries: int = 1
    harness_tool_retry_backoff_seconds: float = 0.5
    # 连续多少步“重复工具调用且无新增证据”后提前收尾，防止空转
    harness_no_progress_limit: int = 2
    # harness 直连日志类工具时，对超大输出走 analyze_logs 聚类摘要而非硬截断
    harness_log_pipeline_enabled: bool = True
    # 证据自检为低置信度/有缺口时，在最终答案前显式插入缺口声明（纠正型自检）
    harness_corrective_verify_enabled: bool = True
    # 规划/自检是否改用 LLM 驱动（默认关闭，回退到确定性规则版）
    harness_llm_planning_enabled: bool = False
    harness_llm_verify_enabled: bool = False

    # 日志分析管线（处理上万行日志）
    # 进入聚类前允许处理的最大原始行数（超出按时间倒序截断并提示）
    log_max_lines: int = 20000
    # 送入 LLM 的字符预算（近似 token 控制），超出触发 Map-Reduce 摘要
    log_token_budget: int = 12000
    # Map-Reduce 分块的字符大小
    log_chunk_size: int = 8000
    # 聚类后保留的 Top 模板数量
    log_top_patterns: int = 30
    # 日志摘要使用的模型（留空则用默认 llm_model）
    log_summary_model: str = ""

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # RAG 配置
    rag_top_k: int = 3
    rag_model: str = "gpt-5.4"  # 使用快速响应模型，不带扩展思考
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

    # Prometheus（告警 API 工具 query_prometheus_alerts 使用；
    # 指标 PromQL 相关的 PROMETHEUS_CPU_QUERY / PROMETHEUS_MEMORY_QUERY / PROMETHEUS_RATE_WINDOW
    # 由独立的 monitor MCP Server 读取，详见 .env.example 与 mcp_servers/README.md）
    prometheus_base_url: str = "http://127.0.0.1:9090"
    prometheus_request_timeout: float = 10.0

    # Provider modes
    # monitor_target_mode: self|local 用本机 psutil；prometheus 让 monitor MCP 的指标工具走真实 PromQL
    monitor_target_mode: str = "self"
    log_provider: str = "local"

    # Short-term conversation checkpoint storage path.
    checkpoint_db_path: str = "volumes/checkpoints.db"

    # Long-term memory
    memory_db_path: str = "volumes/long_term_memory.db"
    project_id: str = "super_biz_agent"
    long_term_memory_enabled: bool = True
    experience_memory_collection: str = "experience_memory"
    experience_memory_top_k: int = 3
    experience_memory_similarity_threshold: float = 0.78
    experience_memory_high_confidence: float = 0.8
    experience_memory_weak_confidence: float = 0.4
    service_knowledge_enabled: bool = True
    user_preferences_enabled: bool = True
    auth_token_secret: str = "dev-auth-token-secret"

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
