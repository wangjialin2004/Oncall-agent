# 本地 BGE-M3 Embedding 替换 DashScope

**日期**：2026-07-16  
**状态**：已实现并验证  
**分支**：`codex/publish-current-worktree` → 推送 origin 同名分支

## 问题

1. `vector_embedding_service` 写死 DashScope OpenAI 兼容端点，import 时强制校验 `DASHSCOPE_API_KEY`，导致 ci-smoke 收集失败。
2. 用户要求默认改为 **本地 BGE-M3**（`BAAI/bge-m3`），并推送到远程当前分支。

## 决策与默认

| 项 | 决策 |
|---|---|
| 默认 provider | `local_bge_m3`（FlagEmbedding / 本地模型） |
| 降级开关 | `EMBEDDING_PROVIDER=dashscope` 可回退（符合新能力降级约定） |
| 维度 | 默认 **1024**（与现有 Milvus `biz` / `experience_memory` 对齐） |
| 初始化 | **lazy**；import 不下载模型、不要求 API key |
| 接口 | 保持 `embed_query` / `embed_documents` duck type，调用方尽量不动 |
| 推送 | 当前分支 `codex/publish-current-worktree` |
| 非目标 | 不在本次自动重建生产向量库；文档要求切模后强制 reindex |

## 范围

### 改动

1. `app/services/vector_embedding_service.py` — provider 工厂 + lazy singleton + LocalBgeM3 / DashScope 实现
2. `app/config.py` — `EMBEDDING_*` 配置
3. `pyproject.toml` — 可选依赖 `embedding`（FlagEmbedding 等），避免强行拖垮 pure unit CI
4. `.env.example` / README 相关配置段 / health 状态
5. `app/core/milvus_client.py` / experience `VECTOR_DIM` 读配置（可选但推荐）
6. 单测：无 key 可 import；provider 选择；local embed 可 mock
7. ci-smoke 无 key 可收集通过
8. `plan/` 进度记录；`AGENTS.md` 索引

### 非目标

- 不在 CI 下载真实 BGE 模型
- 不自动 drop 生产 collection（同 dim 下旧向量语义仍错误，需运维 reindex）
- 不改 LLM chat 路径

## 验证

```bash
# 无 key 可 import / 收集
python -c "from app.services.vector_embedding_service import get_vector_embedding_service; print('ok')"
python -m pytest tests/test_vector_embedding_service.py tests/test_m1_close_the_loop.py tests/test_m1_w2_context_checkpoint.py tests/test_m1_w3_replan_latency.py tests/test_m1_w4_latency_exit.py tests/test_harness_verifier.py tests/test_harness_checkpoint.py tests/test_context_integration.py -q --tb=line --no-cov

# 有模型时（本机）
# EMBEDDING_PROVIDER=local_bge_m3
# python scripts/rebuild_vector_collection.py --yes
# POST /api/memory/experiences/rebuild-index
```

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| 旧向量与 BGE 语义空间不兼容 | 文档强制 dual reindex；同 dim 不会触发 auto-drop |
| torch / FlagEmbedding 体积大 | 放 optional-deps `embedding`；lazy load |
| CI 下载模型 | unit 测 mock；import 不触发 load |
| 本地无 GPU 慢 | 文档说明 CPU 可用；batch 可配 |

回滚：`EMBEDDING_PROVIDER=dashscope` + 恢复 `DASHSCOPE_API_KEY`，并 reindex 回 DashScope 向量。

## 切模后运维（必做）

```bash
python scripts/rebuild_vector_collection.py --yes
# 再重建经验记忆索引（API 或对应 service）
```

## 验证证据（2026-07-16）

```text
python -c "from app.services.vector_embedding_service import vector_embedding_service, get_vector_embedding_service; ..."
# import ok _LazyEmbeddingProxy
# provider client LocalBgeM3Embeddings dims 1024

python -m pytest \
  tests/test_vector_embedding_service.py \
  tests/test_m1_close_the_loop.py \
  tests/test_m1_w2_context_checkpoint.py \
  tests/test_m1_w3_replan_latency.py \
  tests/test_m1_w4_latency_exit.py \
  tests/test_harness_verifier.py \
  tests/test_harness_checkpoint.py \
  tests/test_context_integration.py \
  -q --tb=line --no-cov
# 56 passed
```

### 落地要点

- 默认 `EMBEDDING_PROVIDER=local_bge_m3`；`dashscope` 保留为降级
- `vector_embedding_service` 改为 lazy proxy，import 不再要求 API key
- `pip install -e ".[embedding]"` 安装 FlagEmbedding/torch；CI 不装、不下载模型
- 同 dim 切换仍须 dual reindex（语义空间不同）
