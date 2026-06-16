import importlib
import sys
import warnings

from pydantic.warnings import PydanticDeprecatedSince20


def _reload_without_pydantic_config_warnings(module_name: str):
    sys.modules.pop(module_name, None)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        importlib.import_module(module_name)

    pydantic_config_warnings = [
        warning
        for warning in captured
        if issubclass(warning.category, PydanticDeprecatedSince20)
        and "class-based `config`" in str(warning.message)
    ]
    assert pydantic_config_warnings == []


def test_models_do_not_emit_class_based_config_deprecation_warnings():
    _reload_without_pydantic_config_warnings("app.models.aiops")
    _reload_without_pydantic_config_warnings("app.models.document")
    _reload_without_pydantic_config_warnings("app.models.request")
