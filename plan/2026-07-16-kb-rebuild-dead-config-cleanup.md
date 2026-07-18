# 知识库 BGE 重建 + 死配置清理

**日期**：2026-07-16  
**状态**：已完成并验证

## 知识库重建

```bash
pip install -e ".[embedding]"
docker compose -f vector-database.yml up -d
python scripts/rebuild_vector_collection.py --yes --directory ./aiops-docs
```

结果：

- provider: `local_bge_m3` / `BAAI/bge-m3` / dim 1024
- 5 文件全部成功，0 失败
- collection `biz`：**21 entities**
- 检索冒烟：`CPU 使用率过高如何排查` → top hit `cpu_high_usage.md`（score≈0.56 L2）

## 死配置清理（代码零引用）

从 `app/config.py` + `.env.example` 删除：

| 字段 | 原因 |
|---|---|
| `harness_knowledge_soft_timeout_seconds` | 仅文档/示例，未接线 |
| `harness_anti_pattern_tool_hint` | 计划项，代码未读 |
| `harness_context_llm_patch_enabled` | 默认 false 且无读取 |
| `rag_model` | 无任何 RAG 代码使用 |
| `harness_checkpoint_max_idempotent_tools` | 无读取 |
| `service_knowledge_enabled` | 计划曾要求门控，运行时工具始终注册 |

Settings 字段：176 → 170。  
pilot 文档同步去掉失效开关引用。

## 验证

- harness import ok
- `tests/test_vector_embedding_service.py` + m1 close/w2：**20 passed**

## 非目标

- 未改历史 plan 正文里的设计叙述
- 未重建 experience_memory（本次仅知识库 `biz`）
