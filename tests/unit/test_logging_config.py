import json
import logging

from app.core.logging_config import JsonLogFormatter, configure_logging


def test_json_log_formatter_keeps_correlation_fields_and_redacts_secrets() -> None:
    record = logging.LogRecord(
        name="app.services.message_service",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="runtime completed",
        args=(),
        exc_info=None,
    )
    record.session_id = "sess-123"
    record.run_id = "run-123"
    record.turn_id = "turn-123"
    record.document_id = "doc-123"
    record.api_key = "sk-secret"
    record.color_message = "runtime \x1b[36mcompleted\x1b[0m"

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["message"] == "runtime completed"
    assert payload["session_id"] == "sess-123"
    assert payload["run_id"] == "run-123"
    assert payload["turn_id"] == "turn-123"
    assert payload["document_id"] == "doc-123"
    assert payload["api_key"] == "[redacted]"
    assert "color_message" not in payload


def test_configure_logging_updates_uvicorn_handlers() -> None:
    root_logger = logging.getLogger()
    uvicorn_logger = logging.getLogger("uvicorn.access")
    original_root_handlers = root_logger.handlers[:]
    original_root_level = root_logger.level
    original_uvicorn_handlers = uvicorn_logger.handlers[:]
    original_uvicorn_level = uvicorn_logger.level
    handler = logging.StreamHandler()

    try:
        uvicorn_logger.handlers[:] = [handler]

        configure_logging(level="INFO", log_format="json")

        assert isinstance(handler.formatter, JsonLogFormatter)
        assert uvicorn_logger.level == logging.INFO
    finally:
        root_logger.handlers[:] = original_root_handlers
        root_logger.setLevel(original_root_level)
        uvicorn_logger.handlers[:] = original_uvicorn_handlers
        uvicorn_logger.setLevel(original_uvicorn_level)
