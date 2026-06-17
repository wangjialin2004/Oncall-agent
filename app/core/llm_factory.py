"""Factory for the provider-neutral LLM client."""

from app.config import config
from app.core.llm_client import LLMClient, LLMClientConfig


class LLMFactory:
    """Create application-owned OpenAI-compatible LLM clients."""

    DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @staticmethod
    def create_chat_model(
        model: str | None = None,
        temperature: float = 0.7,
        streaming: bool = True,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> LLMClient:
        client_config = LLMClientConfig.from_settings(config)
        if model is not None or base_url is not None or api_key is not None:
            client_config = LLMClientConfig(
                provider=client_config.provider,
                base_url=base_url or client_config.base_url,
                api_key=api_key or client_config.api_key,
                model=model or client_config.model,
                timeout=client_config.timeout,
                default_headers=client_config.default_headers,
            )

        # Kept for call-site compatibility. Temperature and streaming are set
        # per request on LLMClient.complete().
        _ = (temperature, streaming)
        return LLMClient(client_config)


llm_factory = LLMFactory()
