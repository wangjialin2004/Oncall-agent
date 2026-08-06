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
    static_serve_enabled: bool = False
    # Runtime observability access. Production defaults to internal-only;
    # bearer is for a protected Prometheus scrape path.
    metrics_access_mode: str = "internal"  # internal | bearer | public
    metrics_bearer_token: str = ""
    metrics_public_debug_enabled: bool = False
    health_details_enabled: bool = False

    # DashScope 配置（LLM 遗留回退 + embedding=dashscope 时使用）
    dashscope_api_key: str = ""  # 默认空字符串，实际使用需从环境变量加载
    dashscope_model: str = ""
    dashscope_embedding_model: str = "text-embedding-v4"  # v4 支持多种维度（默认 1024）

    # Embedding provider configuration.
    # local_bge_m3: local BAAI/bge-m3 via FlagEmbedding (default; no API key).
    # dashscope: OpenAI-compatible DashScope embeddings (requires DASHSCOPE_API_KEY).
    embedding_provider: str = "local_bge_m3"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    # Optional local path/HF cache id; empty → use embedding_model.
    embedding_model_path: str = ""
    embedding_device: str = ""  # empty = auto (cuda if available else cpu)
    embedding_batch_size: int = 12
    embedding_normalize: bool = True
    embedding_use_fp16: bool = True
    # DashScope-compatible endpoint overrides (only when embedding_provider=dashscope).
    embedding_dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # Generic LLM provider configuration. When unset, the custom LLM client
    # falls back to the legacy DashScope settings above.
    llm_provider: str = "openai"  # openai | azure | custom
    llm_base_url: str = "https://dasuapi.com/v1"
    llm_api_key: str = "get.env('LLM_API_KEY')"
    llm_model: str = "gpt-5.6-luna"
    llm_timeout: float = 60.0
    # 瞬时错误（429 / 5xx / 网络超时）的指数退避重试次数；鉴权错误不重试
    llm_max_retries: int = 2
    llm_retry_base_delay: float = 0.5

    # Router + 专家 Agent 配置
    # 语义路由低于该置信度时，旧行为回退 diagnosis；新行为见 low_confidence_keep 开关
    router_min_confidence: float = 0.55
    # 将关键词分为强/弱两层；弱词只作为语义路由提示，避免单个泛化词误导路由
    router_keyword_tiering_enabled: bool = True
    # 语义分类器允许同时返回主路由 + 多个辅助路由；关闭后退化为单标签语义分类
    router_multilabel_enabled: bool = True
    # 语义误把“具体目标 + 事故信号”归为 knowledge 时，提升为 diagnosis；
    # 关闭后保留原始语义路由，便于一键回滚。
    router_concrete_incident_override_enabled: bool = True
    # 「继续/然后呢」等短句续聊继承上一轮 conversation route；关则每轮独立路由
    router_continuation_inherit_enabled: bool = True
    # 低置信时尽量保留语义 route（knowledge 非故障等），不全量 diagnosis；关则恢复旧回退
    router_low_confidence_keep_semantic_enabled: bool = True
    # knowledge 轻问题（问候/身份/纯解释）跳过 re_evidence/replan 与硬证据要求
    harness_knowledge_light_path_enabled: bool = True
    # 单个专家执行超时（秒），超时返回降级答案
    expert_timeout_seconds: float = 120.0

    # Harness 主循环配置。
    # 说明：HTTP 入口 /api/assistant 已固定走 harness_service；本开关主要用于
    # checkpoint 激活等子能力门控，不再作为「新旧双路径」入口开关。
    harness_enabled: bool = True
    harness_max_steps: int = 6
    harness_token_budget: int = 80000
    # 每次发往模型的 messages 体量安全网：超过则压缩最旧的历史/工具消息（保留 tool_call 配对），
    # 防止收尾或多步取证后撞模型上下文上限导致 API 报错；<=0 关闭该裁剪
    harness_message_token_budget: int = 60000
    harness_history_max_turns: int = 16
    # 关闭后退化为旧版“仅按最近 N 轮”截断，便于一键降级
    harness_history_token_window_enabled: bool = True
    # 历史对话注入的独立 token 预算；<=0 时退化为仅按轮数截断
    harness_history_token_budget: int = 6000
    # 单条历史消息过长时折叠，防止少数长答案撑爆上下文
    harness_history_message_max_chars: int = 4000
    harness_attachment_context_max_chars: int = 12000
    harness_attachment_summary_max_chars: int = 600
    harness_attachment_keyword_limit: int = 12
    # 维护更早对话的滚动摘要；异常或关闭时降级为仅最近窗口逐字历史
    harness_rolling_summary_enabled: bool = True
    harness_rolling_summary_max_chars: int = 4000
    # 单次滚动摘要合并时，新增对话输入的独立 token 预算；<=0 关闭该限制
    harness_rolling_summary_input_token_budget: int = 6000
    harness_rolling_summary_model: str = ""
    # 滚动摘要在请求关键路径上同步调用 LLM，单独限时；超时则保留旧摘要不阻塞回答
    harness_rolling_summary_timeout_seconds: float = 20.0
    # harness 主循环总闸门（外层兜底）：要大于 delegate_timeout + step_timeout + 收尾余量
    harness_timeout_seconds: float = 240.0
    # 单步 LLM “下一步判断”独立限时：超时即触发 step_timeout 事件并立即收尾，
    # 避免某一次 stream_chat 慢独占整个总闸门
    harness_step_timeout_seconds: float = 60.0
    # 降级路径（knowledge_expert / raw_vector）独立限时，防止降级再卡死 SSE
    harness_fallback_timeout_seconds: float = 30.0
    harness_mcp_enabled: bool = False
    harness_delegation_enabled: bool = True
    # 路由选中的专项专家是否在主循环开始时被“确定性委派执行”：开启后 harness 作为编排器，
    # 先把核心调查交给被选专家执行，再在其结论与证据上做核对/补充/收尾，而不是自己直接作答。
    # 关闭后退化为旧的软提示行为（是否委派由 harness LLM 自行决定）。
    harness_force_expert_delegation: bool = False
    # 委派子专家的独立超时，避免单个慢子专家吃光父级总超时；超时返回降级结果
    # （略低于 step 预算，保证 close 余地）
    harness_delegate_timeout_seconds: float = 90.0
    # 模型分层：planner 用轻量模型做路由/单步判断，reasoner 用深度模型做最终收口/证据自检
    llm_planner_model: str = ""
    llm_reasoner_model: str = "gpt-5.6-luna"
    harness_tool_timeout_seconds: float = 30.0
    harness_tool_collection_timeout_seconds: float = 5.0
    harness_tool_max_output_chars: int = 6000
    # 工具瞬时错误（超时/网络/5xx）的有限重试次数；鉴权/权限类错误不重试
    harness_tool_max_retries: int = 1
    harness_tool_retry_backoff_seconds: float = 0.5
    # Omit a terminal non-retryable tool from later model menus in one run.
    # False restores the previous behavior of re-offering it.
    harness_failed_tool_suppression_enabled: bool = True
    # 连续多少步“重复工具调用且无新增证据”后提前收尾，防止空转
    harness_no_progress_limit: int = 2
    # harness 直连日志类工具时，对超大输出走 analyze_logs 聚类摘要而非硬截断
    harness_log_pipeline_enabled: bool = True
    # 证据自检为低置信度/有缺口时，在最终答案前显式插入缺口声明（纠正型自检）
    harness_corrective_verify_enabled: bool = True
    # 低置信 / 有缺口时，在定稿前最多再跑 N 轮「补取证」tool loop（M1 Close the Loop）
    harness_re_evidence_enabled: bool = True
    harness_re_evidence_max_rounds: int = 1
    # Once user-visible content has streamed, keep re-evidence/replan replacement
    # prose in complete.answer instead of appending a second answer body.
    harness_final_answer_replacement_enabled: bool = True
    # 计划 required_evidence 与成功工具名做类型细匹配（M1 W2）
    harness_evidence_match_enabled: bool = True
    # Public SSE progress details: safe plan/result/evidence summaries. Set
    # false to restore the minimal pre-detail public event contract.
    harness_public_progress_details_enabled: bool = True
    # mid-loop / post re-evidence 规则 replan（M1 W3）
    harness_replan_enabled: bool = True
    harness_replan_max_times: int = 1
    # knowledge/clarify 等简单路由动态降低 max_steps（M1 W3）
    harness_dynamic_max_steps: bool = True
    # 同一步多个只读 tool_call 并行执行（M1 W3）
    harness_parallel_tool_calls: bool = True
    # knowledge 路由在已有成功知识类工具后强制无工具收口（M1 W4 时延）
    harness_knowledge_early_close: bool = True
    # 按路由收紧步数/软超时（knowledge 更短；M1 W4）
    harness_route_timeout_profile: bool = True
    # M2 W5：仅在 route timeout profile 打开时，诊断路由最多走这些主循环步数。
    # 设为当前 HARNESS_MAX_STEPS 值或关闭 profile 即可回退旧预算。
    harness_diagnosis_max_steps: int = 3
    # M2 W5: an investigation with read-only evidence closes to the final answer
    # instead of paying for further planner turns. Gated by the route timeout
    # profile; false restores the prior loop.
    harness_investigation_evidence_early_close: bool = True
    # M2 W5：delegate_to_expert 的独立工具决策轮数；2/3 可回滚到更宽预算。
    # 该值按调用传入，不能修改 registry 单例专家的共享实例属性。
    harness_delegate_max_tool_rounds: int = 1
    # M2 W5：委派专家首批工具完成后只回传证据给父 Harness 统一总结。
    # 关闭后恢复专家自己的无工具总结调用。
    harness_delegate_evidence_only: bool = True
    # M2 W6：跨域并行委派 fan-out（delegate_parallel）；false 不注册/拒绝该工具。
    harness_parallel_delegation_enabled: bool = True
    harness_parallel_max_experts: int = 3
    # M2 W6：router aux_routes 自动 probe；off|serial|parallel（非法值回退 off）。
    router_aux_execution_mode: str = "parallel"
    router_aux_max_probes: int = 2
    # M2 W6：complete 时 best-effort 导出轨迹（默认关，避免占盘/敏感信息）。
    harness_trace_export_enabled: bool = False
    harness_trace_export_dir: str = "volumes/traces"
    # M3 W11：trace 采样率 0–1；仅在 export enabled 时生效。默认 0=全不写。
    harness_trace_sample_rate: float = 0.0
    # M3 W11：可选 OTLP endpoint；空字符串 = 关闭（零依赖）。
    otel_exporter_otlp_endpoint: str = ""
    # M2 W7：专家/委派共用 sub_harness 内核（事件 payload 标记；循环体始终 shared）。
    harness_shared_kernel_delegation: bool = True
    # M2 W7 WP-C4：变更源策略 unavailable|future（无真实源前禁止 pretend available）。
    change_source_policy: str = "unavailable"
    # M3 W9：跨域 diagnosis / prefer_parallel 时框架 seed delegate_parallel（可关）。
    harness_force_parallel_on_cross_domain: bool = True
    # M3 W9：主 focus 调查工具失败时即使已有弱成功证据也允许 replan 一次。
    harness_replan_on_primary_fail: bool = True
    # M3 W9：同名只读工具成功次数上限（>0 启用）；抑制 S5 类重复 search_app_logs。
    harness_slow_path_tool_cap: int = 2
    # M3 W9：外层超时前若已有证据，优先用已有证据收口，避免 degraded fallback 文案。
    harness_timeout_soft_close_enabled: bool = True
    # M3 W9：最终答案结构化 suggested_actions[]（只读建议，确认不执行）。
    hitl_suggested_actions_enabled: bool = True
    # M3 W9：升级联系人，空则输出标准「未配置」块。格式 name|channel;name2|channel2
    # M3 W10：成功 run 半自动蒸馏（默认 draft+confirm，禁止静默全量入库）。
    long_term_memory_distill_enabled: bool = True
    long_term_memory_auto_distill: bool = False
    long_term_memory_distill_require_confirm: bool = True
    # medium|high|low — 低于阈值不产生成功经验 draft。
    long_term_memory_auto_distill_min_confidence: str = "medium"
    # M3 W10：失败/空证据反模式捕获与召回提示。
    harness_anti_pattern_capture_enabled: bool = True
    # 规划/自检是否改用 LLM 驱动（默认关闭，回退到确定性规则版）
    harness_llm_planning_enabled: bool = False
    harness_llm_verify_enabled: bool = False

    # Stateful Harness context renderer（默认开启）。ContextRepository 始终拥有
    # 运行时加载和持久化；False 仅选择兼容的 LLM 视图渲染，不会回退存储路径。
    harness_stateful_context_enabled: bool = True
    # rebuild-from-turns：从 recent_turns 重建白板（resume / 冷启动）。
    harness_context_rebuild_from_turns_enabled: bool = True
    harness_context_view_token_budget: int = 4000
    harness_context_redis_ttl_seconds: int = 86400
    harness_context_db_snapshot_enabled: bool = True
    harness_context_patch_history_limit: int = 200
    harness_context_tools_enabled: bool = True
    # When the whiteboard view + turn history are already in the model prompt,
    # omit context_read by default (note/attachment tools still register).
    # Set true only for debugging section-level re-reads.
    harness_context_read_when_view_injected: bool = False
    # Context deduplication / attachment prompt controls.  Each switch is
    # independently reversible so a rollout can fall back without changing
    # the stateful-context storage contract.
    harness_context_intent_omit_from_view_enabled: bool = True
    harness_attachment_prompt_mode: str = "summary"  # full | summary | index
    harness_preference_inject_mode: str = "router_and_harness"  # router_and_harness | harness_only
    harness_context_view_dedup_tool_evidence: bool = True
    harness_context_size_metrics_enabled: bool = False
    # Unified ContextRepository (single load + atomic turn/projection commit).
    # 日志分析管线（处理上万行日志）
    # 进入聚类前允许处理的最大原始行数（超出按时间倒序截断并提示）
    # 注意：原 20000 会触发 5~10 个 Map-Reduce chunk（每个 10~30s）远超总闸门
    log_max_lines: int = 8000
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
    rag_retrieval_mode: str = "dense"  # dense | bm25 | hybrid
    rag_dense_weight: float = 0.7
    rag_bm25_weight: float = 0.3
    rag_hybrid_ranker: str = "weighted"  # weighted | rrf
    rag_rrf_k: int = 60  # RRF constant, only used when rag_hybrid_ranker == "rrf"
    rag_dense_vector_field: str = "vector"
    rag_sparse_vector_field: str = "sparse_vector"
    # Tenant scope is fail-closed by default. biz_v2 is the scoped collection;
    # set RAG_COLLECTION_NAME=biz only for an isolated rollback/observation run.
    rag_tenant_scope_enabled: bool = True
    rag_allow_legacy_unscoped: bool = False
    rag_collection_name: str = "biz_v2"

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

    # Redis client (used by the harness checkpoint subsystem).
    redis_enabled: bool = True
    # Credentials must come from REDIS_URL in the environment/secret manager;
    # never ship a password in the code default.
    redis_url: str = "redis://localhost:6379/0"
    redis_namespace: str = "super_biz_agent"
    redis_socket_timeout: float = 5.0
    redis_protocol: int = 2

    # Harness loop checkpoint (step-level, Redis-backed).
    # Only effective when all three of harness_enabled, harness_checkpoint_enabled,
    # and redis_enabled are True. Resume restores state/context and continues from
    # the next incomplete step; historical tool calls are not re-executed.
    # harness_checkpoint_replay is retained for compatibility (both true/false now
    # continue by default). Request body checkpoint_replay=False can still force
    # close-only finalization when harness_checkpoint_request_override_enabled.
    harness_checkpoint_enabled: bool = True
    harness_checkpoint_ttl_seconds: int = 1800
    harness_checkpoint_replay: bool = False
    # Request-level overrides are intentionally disabled for normal product traffic.
    harness_checkpoint_request_override_enabled: bool = False
    harness_eval_hooks_enabled: bool = False

    # Long-term memory
    memory_db_path: str = "volumes/long_term_memory.db"
    # Phase-gated switch: when enabled, domain services verify the explicit
    # migration version and never run their legacy first-request DDL path.
    db_schema_enforcement_enabled: bool = False
    project_id: str = "super_biz_agent"
    long_term_memory_enabled: bool = True
    experience_memory_collection: str = "experience_memory"
    experience_memory_top_k: int = 3
    experience_memory_similarity_threshold: float = 0.78
    experience_memory_high_confidence: float = 0.8
    experience_memory_weak_confidence: float = 0.4
    user_preferences_enabled: bool = True

    # Auth / CORS (pilot baseline)
    # HMAC signing secret for access tokens. MUST be overridden in any shared env.
    auth_token_secret: str = "dev-auth-token-secret"
    # Access token TTL in seconds. 0 disables expiry checks (not recommended).
    auth_token_ttl_seconds: int = 86400
    # Fixed account table: "user1:pass1,user2:pass2". Empty rejects all logins.
    auth_users: str = "admin:admin"
    # Optional per-user role/project maps: ``alice:curator,bob:admin`` and
    # ``alice:project-a``. Unmapped authenticated users are operators in the
    # configured default project.
    auth_user_roles: str = ""
    auth_user_projects: str = ""
    # Phase 1 authenticates all business APIs; Phase 2 enables curator/admin
    # gates after role mappings are deployed.
    auth_role_enforcement_enabled: bool = False
    # Comma-separated browser origins. Use "*" only for pure local demos.
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Process-local L1 cache for the long-term memory subsystem
    # (plan/memory-cache-layer.md §3.4 / §3.7). Disable via env
    # ``MEMORY_CACHE_ENABLED=0`` to fall back to the raw SQLite path.
    memory_cache_enabled: bool = True
    memory_cache_max_entries: int = 1024
    memory_cache_ttl_user_preference_seconds: float = 300.0
    memory_cache_ttl_experience_seconds: float = 60.0
    memory_cache_ttl_service_knowledge_seconds: float = 60.0

    # File storage backend.
    # ``local`` uses the local filesystem; ``minio|oss|s3|cos`` use the
    # S3-compatible adapter against the configured endpoint/bucket.
    storage_backend: str = "local"
    storage_local_root: str = "volumes/file_storage"
    storage_cache_dir: str = "volumes/file_storage/_cache"
    storage_max_file_size_mb: int = 50
    storage_allowed_extensions: str = ".txt,.text,.md,.markdown,.pdf,.docx"

    # S3-compatible shared settings (used when storage_backend != local).
    storage_endpoint: str = ""
    storage_region: str = "us-east-1"
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_bucket: str = ""
    storage_use_ssl: bool = False
    storage_url_style: str = "path"  # path | virtual-hosted
    storage_public_url_base: str = ""

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
            },
        }

    @property
    def auth_user_map(self) -> dict[str, str]:
        """Parse AUTH_USERS into username -> password."""
        mapping: dict[str, str] = {}
        for item in (self.auth_users or "").split(","):
            entry = item.strip()
            if not entry or ":" not in entry:
                continue
            username, password = entry.split(":", 1)
            username = username.strip()
            password = password.strip()
            if username and password:
                mapping[username] = password
        return mapping

    @staticmethod
    def _parse_user_map(value: str, separator: str = ":") -> dict[str, str]:
        result: dict[str, str] = {}
        for item in (value or "").split(","):
            if separator not in item:
                continue
            username, mapped = item.split(separator, 1)
            username, mapped = username.strip(), mapped.strip()
            if username and mapped:
                result[username] = mapped
        return result

    def role_for_user(self, username: str) -> str:
        role = self._parse_user_map(self.auth_user_roles).get(username.strip(), "operator")
        return role if role in {"operator", "curator", "admin"} else "operator"

    def project_for_user(self, username: str) -> str:
        return self._parse_user_map(self.auth_user_projects).get(username.strip(), self.project_id)

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS_ALLOW_ORIGINS into a list for CORSMiddleware."""
        raw = (self.cors_allow_origins or "").strip()
        if not raw:
            return ["http://localhost:5173"]
        if raw == "*":
            return ["*"]
        return [part.strip() for part in raw.split(",") if part.strip()]


# 全局配置实例
config = Settings()
