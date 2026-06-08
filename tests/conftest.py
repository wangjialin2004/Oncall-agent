import sys
import types

import httpx
import pytest


class _DummyFastMCP:
    def __init__(self, *_args, **_kwargs):
        pass

    def tool(self, *_args, **_kwargs):
        def decorator(func):
            return func

        return decorator

    def run(self, *_args, **_kwargs):
        return None


if "fastmcp" not in sys.modules:
    fastmcp_module = types.ModuleType("fastmcp")
    fastmcp_module.FastMCP = _DummyFastMCP
    sys.modules["fastmcp"] = fastmcp_module

from app.main import app  # noqa: E402


@pytest.fixture
async def api_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
