from pathlib import Path


def test_tests_do_not_use_deprecated_fastapi_testclient():
    deprecated_import = "fastapi" + ".testclient"
    offenders = []
    for path in Path("tests").glob("test_*.py"):
        if deprecated_import in path.read_text(encoding="utf-8"):
            offenders.append(str(path))

    assert offenders == []
