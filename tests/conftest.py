import sys
import types


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
