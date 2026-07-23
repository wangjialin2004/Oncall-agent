"""Provider-neutral vector embedding service.

Default provider is local BGE-M3 (``BAAI/bge-m3``). DashScope remains available
via ``EMBEDDING_PROVIDER=dashscope`` as a degradation switch.

The module-level ``vector_embedding_service`` is a lazy proxy: importing this
module never downloads models or requires API keys. The real client is created
on first ``embed_query`` / ``embed_documents`` call.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from app.config import config


@runtime_checkable
class EmbeddingService(Protocol):
    """Duck-typed embedding API used by store/search/experience index."""

    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _mask_api_key(api_key: str) -> str:
    if len(api_key) > 8:
        return f"{api_key[:8]}...{api_key[-4:]}"
    return "***"


def _normalize_provider(raw: str | None) -> str:
    value = str(raw or "").strip().lower().replace("-", "_")
    aliases = {
        "bge": "local_bge_m3",
        "bge3": "local_bge_m3",
        "bge_m3": "local_bge_m3",
        "local": "local_bge_m3",
        "local_bge": "local_bge_m3",
        "local_bge_m3": "local_bge_m3",
        "flagembedding": "local_bge_m3",
        "dashscope": "dashscope",
        "qwen": "dashscope",
        "aliyun": "dashscope",
    }
    return aliases.get(value, value or "local_bge_m3")


def _running_under_wsl() -> bool:
    """Best-effort WSL detection (kernel name or /proc/version marker)."""

    try:
        import platform
        import re

        if re.search(r"microsoft|wsl", platform.release(), flags=re.I):
            return True
    except Exception:
        pass
    try:
        with open("/proc/version", encoding="utf-8", errors="ignore") as fh:
            return "microsoft" in fh.read().lower()
    except Exception:
        return False


class DashScopeEmbeddings:
    """DashScope OpenAI-compatible text embedding client (degradation path)."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ):
        if not api_key or api_key in {"your-api-key-here", "your-dashscope-api-key-here"}:
            raise ValueError("请设置环境变量 DASHSCOPE_API_KEY")

        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.dimensions = dimensions
        logger.info(
            "DashScope Embeddings initialized - model: {}, dims: {}, api_key: {}",
            model,
            dimensions,
            _mask_api_key(api_key),
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            logger.info("Embedding {} documents via DashScope", len(texts))
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
                dimensions=self.dimensions,
                encoding_format="float",
            )
            embeddings = [item.embedding for item in response.data]
            logger.debug(
                "Embedded batch complete, dims={}",
                len(embeddings[0]) if embeddings else 0,
            )
            return embeddings
        except Exception as e:
            logger.error("Document embedding failed: {}", e)
            raise RuntimeError(f"Document embedding failed: {e}") from e

    def embed_query(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("查询文本不能为空")

        try:
            logger.debug("Embedding query via DashScope, length={}", len(text))
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimensions,
                encoding_format="float",
            )
            embedding = response.data[0].embedding
            logger.debug("Query embedding complete, dims={}", len(embedding))
            return embedding
        except Exception as e:
            logger.error("Query embedding failed: {}", e)
            raise RuntimeError(f"Query embedding failed: {e}") from e


class LocalBgeM3Embeddings:
    """Local BGE-M3 dense embeddings via FlagEmbedding."""

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-m3",
        model_path: str = "",
        dimensions: int = 1024,
        device: str = "",
        batch_size: int = 12,
        normalize: bool = True,
        use_fp16: bool = True,
    ) -> None:
        self.model_name = (model_path or model_name or "BAAI/bge-m3").strip()
        self.dimensions = int(dimensions or 1024)
        self.device = (device or "").strip()
        self.batch_size = max(1, int(batch_size or 12))
        self.normalize = bool(normalize)
        self.use_fp16 = bool(use_fp16)
        self._model: Any | None = None
        self._model_lock = threading.Lock()
        logger.info(
            "Local BGE-M3 embedding client configured - model: {}, dims: {}, "
            "device: {}, batch_size: {}, fp16: {}",
            self.model_name,
            self.dimensions,
            self.device or "auto",
            self.batch_size,
            self.use_fp16,
        )

    def _resolve_device(self) -> str | None:
        if self.device:
            return self.device
        # WSL2 + NVIDIA libcuda has produced fatal SIGSEGV during BGE-M3
        # encode in this environment (see dmesg: segfault in libcuda.so).
        # Prefer CPU by default on WSL; operators can still force cuda via
        # EMBEDDING_DEVICE=cuda after validating their driver stack.
        if _running_under_wsl():
            logger.warning(
                "WSL detected; defaulting local BGE-M3 to device=cpu to avoid "
                "libcuda segfaults. Set EMBEDDING_DEVICE=cuda to override."
            )
            return "cpu"
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                from FlagEmbedding import BGEM3FlagModel
            except ImportError as exc:
                raise RuntimeError(
                    "本地 BGE-M3 需要安装 embedding 可选依赖："
                    'pip install -e ".[embedding]" '
                    "（或 pip install 'FlagEmbedding>=1.2.0'）"
                ) from exc

            device = self._resolve_device()
            logger.info(
                "Loading local BGE-M3 model '{}' on device={}",
                self.model_name,
                device or "auto",
            )
            kwargs: dict[str, Any] = {"use_fp16": self.use_fp16}
            if device:
                # FlagEmbedding accepts device as constructor kw on recent versions;
                # older versions ignore unknown kwargs — fall back silently.
                kwargs["device"] = device
            try:
                self._model = BGEM3FlagModel(self.model_name, **kwargs)
            except TypeError:
                kwargs.pop("device", None)
                self._model = BGEM3FlagModel(self.model_name, **kwargs)
            logger.info("Local BGE-M3 model loaded: {}", self.model_name)
            return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load_model()
        # BGE-M3 returns dense_vecs under a dict; some versions may return ndarray directly.
        raw = model.encode(
            texts,
            batch_size=self.batch_size,
            max_length=8192,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        if isinstance(raw, dict):
            vectors = raw.get("dense_vecs")
        else:
            vectors = raw
        if vectors is None:
            raise RuntimeError("BGE-M3 encode returned no dense_vecs")

        result: list[list[float]] = []
        for row in vectors:
            if hasattr(row, "tolist"):
                values = [float(x) for x in row.tolist()]
            else:
                values = [float(x) for x in row]
            if self.normalize:
                values = _l2_normalize(values)
            if self.dimensions and len(values) != self.dimensions:
                # Keep going but surface mismatch clearly — collection schema depends on dim.
                logger.warning(
                    "BGE-M3 vector dim {} != configured embedding_dim {}",
                    len(values),
                    self.dimensions,
                )
            result.append(values)
        return result

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            logger.info("Embedding {} documents via local BGE-M3", len(texts))
            embeddings = self._encode([str(t or "") for t in texts])
            logger.debug(
                "Embedded batch complete, dims={}",
                len(embeddings[0]) if embeddings else 0,
            )
            return embeddings
        except Exception as e:
            logger.error("Document embedding failed: {}", e)
            raise RuntimeError(f"Document embedding failed: {e}") from e

    def embed_query(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("查询文本不能为空")
        try:
            logger.debug("Embedding query via local BGE-M3, length={}", len(text))
            embeddings = self._encode([text])
            embedding = embeddings[0]
            logger.debug("Query embedding complete, dims={}", len(embedding))
            return embedding
        except Exception as e:
            logger.error("Query embedding failed: {}", e)
            raise RuntimeError(f"Query embedding failed: {e}") from e


def _l2_normalize(values: list[float]) -> list[float]:
    norm_sq = sum(v * v for v in values)
    if norm_sq <= 0:
        return values
    norm = norm_sq**0.5
    return [v / norm for v in values]


def new_embedding_service(
    *,
    provider: str | None = None,
    settings: Any | None = None,
) -> EmbeddingService:
    """Create an embedding client from settings (no global cache)."""

    cfg = settings or config
    chosen = _normalize_provider(provider if provider is not None else cfg.embedding_provider)
    dim = int(getattr(cfg, "embedding_dim", 1024) or 1024)

    if chosen == "dashscope":
        model = (
            str(getattr(cfg, "dashscope_embedding_model", "") or "").strip()
            or str(getattr(cfg, "embedding_model", "") or "").strip()
            or "text-embedding-v4"
        )
        return DashScopeEmbeddings(
            api_key=str(getattr(cfg, "dashscope_api_key", "") or ""),
            model=model,
            dimensions=dim,
            base_url=str(
                getattr(cfg, "embedding_dashscope_base_url", "")
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
        )

    if chosen == "local_bge_m3":
        model = (
            str(getattr(cfg, "embedding_model", "") or "").strip() or "BAAI/bge-m3"
        )
        return LocalBgeM3Embeddings(
            model_name=model,
            model_path=str(getattr(cfg, "embedding_model_path", "") or ""),
            dimensions=dim,
            device=str(getattr(cfg, "embedding_device", "") or ""),
            batch_size=int(getattr(cfg, "embedding_batch_size", 12) or 12),
            normalize=bool(getattr(cfg, "embedding_normalize", True)),
            use_fp16=bool(getattr(cfg, "embedding_use_fp16", True)),
        )

    raise ValueError(
        f"未知 EMBEDDING_PROVIDER={chosen!r}；支持 local_bge_m3 | dashscope"
    )


_service_lock = threading.Lock()
_cached_service: EmbeddingService | None = None
_cached_signature: tuple[Any, ...] | None = None


def _settings_signature(cfg: Any = config) -> tuple[Any, ...]:
    return (
        _normalize_provider(getattr(cfg, "embedding_provider", "")),
        str(getattr(cfg, "embedding_model", "") or ""),
        str(getattr(cfg, "embedding_model_path", "") or ""),
        int(getattr(cfg, "embedding_dim", 1024) or 1024),
        str(getattr(cfg, "embedding_device", "") or ""),
        int(getattr(cfg, "embedding_batch_size", 12) or 12),
        bool(getattr(cfg, "embedding_normalize", True)),
        bool(getattr(cfg, "embedding_use_fp16", True)),
        str(getattr(cfg, "dashscope_api_key", "") or ""),
        str(getattr(cfg, "dashscope_embedding_model", "") or ""),
        str(getattr(cfg, "embedding_dashscope_base_url", "") or ""),
    )


def get_vector_embedding_service(*, force_reload: bool = False) -> EmbeddingService:
    """Lazy singleton used by production call sites."""

    global _cached_service, _cached_signature
    signature = _settings_signature()
    if (
        not force_reload
        and _cached_service is not None
        and _cached_signature == signature
    ):
        return _cached_service
    with _service_lock:
        signature = _settings_signature()
        if (
            not force_reload
            and _cached_service is not None
            and _cached_signature == signature
        ):
            return _cached_service
        service = new_embedding_service()
        _cached_service = service
        _cached_signature = signature
        return service


def reset_vector_embedding_service() -> None:
    """Drop cached client (tests / config hot-swap)."""

    global _cached_service, _cached_signature
    with _service_lock:
        _cached_service = None
        _cached_signature = None


class _LazyEmbeddingProxy:
    """Preserve ``vector_embedding_service.embed_*`` import style without eager init."""

    @property
    def dimensions(self) -> int:
        return int(getattr(get_vector_embedding_service(), "dimensions", config.embedding_dim))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return get_vector_embedding_service().embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return get_vector_embedding_service().embed_query(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(get_vector_embedding_service(), name)


# Backwards-compatible module singleton (lazy).
vector_embedding_service = _LazyEmbeddingProxy()
