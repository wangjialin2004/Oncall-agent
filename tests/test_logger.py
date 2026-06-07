import io
import sys

from loguru import logger

from app.utils import logger as logger_module


def test_console_logger_handles_unicode_on_non_utf8_stdout(monkeypatch, capsys):
    raw_stdout = io.BytesIO()
    gbk_stdout = io.TextIOWrapper(raw_stdout, encoding="gbk", errors="strict")

    with monkeypatch.context() as context:
        context.setattr(sys, "stdout", gbk_stdout)
        logger_module.setup_logger()

        logger.info("startup marker 📚")
        gbk_stdout.flush()

        captured = capsys.readouterr()
        assert "Logging error" not in captured.err

    logger_module.setup_logger()
