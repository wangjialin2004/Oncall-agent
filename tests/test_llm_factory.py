from app.core.llm_client import LLMClient
from app.core.llm_factory import LLMFactory


def test_llm_factory_creates_custom_llm_client_from_generic_settings(monkeypatch):
    monkeypatch.setattr("app.core.llm_factory.config.llm_provider", "deepseek")
    monkeypatch.setattr("app.core.llm_factory.config.llm_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr("app.core.llm_factory.config.llm_api_key", "deepseek-key")
    monkeypatch.setattr("app.core.llm_factory.config.llm_model", "deepseek-chat")
    monkeypatch.setattr("app.core.llm_factory.config.llm_timeout", 11.0)

    client = LLMFactory.create_chat_model(temperature=0.1, streaming=True)

    assert isinstance(client, LLMClient)
    assert client.config.provider == "deepseek"
    assert client.config.base_url == "https://api.deepseek.com/v1"
    assert client.config.api_key == "deepseek-key"
    assert client.config.model == "deepseek-chat"
    assert client.config.timeout == 11.0
