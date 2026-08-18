"""The log format is a contract.

Whatever ships these lines to a log aggregator parses them, so the shape is worth
pinning: one JSON object per line, with the fields a query would filter on.
"""

import json
import logging

import pytest

from app.observability import JsonFormatter, configure_logging, request_id


@pytest.fixture
def formatter():
    return JsonFormatter()


def record(**kwargs) -> logging.LogRecord:
    defaults = {
        "name": "linkly.test",
        "level": logging.INFO,
        "pathname": __file__,
        "lineno": 1,
        "msg": "hello %s",
        "args": ("world",),
        "exc_info": None,
    }
    return logging.LogRecord(**{**defaults, **kwargs})


def test_a_line_is_one_json_object(formatter):
    payload = json.loads(formatter.format(record()))
    assert payload["level"] == "info"
    assert payload["logger"] == "linkly.test"
    assert payload["message"] == "hello world"
    assert "ts" in payload


def test_the_request_id_rides_along_when_one_is_set(formatter):
    token = request_id.set("abc-123")
    try:
        assert json.loads(formatter.format(record()))["request_id"] == "abc-123"
    finally:
        request_id.reset(token)


def test_no_request_id_key_when_there_is_none(formatter):
    token = request_id.set(None)
    try:
        assert "request_id" not in json.loads(formatter.format(record()))
    finally:
        request_id.reset(token)


def test_extra_fields_are_merged_in(formatter):
    line = record()
    line.fields = {"status": 404, "path": "/nope"}
    payload = json.loads(formatter.format(line))
    assert payload["status"] == 404
    assert payload["path"] == "/nope"


def test_exceptions_are_carried_as_text(formatter):
    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        import sys

        line = record(exc_info=sys.exc_info(), level=logging.ERROR)

    payload = json.loads(formatter.format(line))
    assert payload["level"] == "error"
    assert "kaboom" in payload["exception"]


def test_unserialisable_values_do_not_break_the_line(formatter):
    line = record()
    line.fields = {"weird": object()}
    assert json.loads(formatter.format(line))["weird"].startswith("<object")


def test_configure_logging_installs_exactly_one_json_handler():
    root = logging.getLogger()
    original = root.handlers[:]
    original_level = root.level
    try:
        configure_logging()
        configure_logging()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers = original
        root.setLevel(original_level)


def test_configure_logging_takes_over_uvicorns_handlers():
    root = logging.getLogger()
    original = root.handlers[:]
    access = logging.getLogger("uvicorn.access")
    access.handlers = [logging.StreamHandler()]
    try:
        configure_logging()
        assert access.handlers == []
        assert access.propagate is True
    finally:
        root.handlers = original
