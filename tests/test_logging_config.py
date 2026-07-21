"""Unit tests for stderr logging configuration."""

import logging
import sys
from collections.abc import Iterator

import pytest

from spdoc_mcp.logging_config import configure_logging


@pytest.fixture
def _restore_root_logging() -> Iterator[None]:
    """Snapshot and restore root logger handlers/level around a test."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    root.handlers[:] = original_handlers
    root.setLevel(original_level)


def _stderr_handlers(root: logging.Logger) -> list[logging.Handler]:
    return [
        handler
        for handler in root.handlers
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr
    ]


@pytest.mark.unit
@pytest.mark.usefixtures("_restore_root_logging")
def test_configure_logging_adds_stderr_handler_at_info() -> None:
    configure_logging()
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert len(_stderr_handlers(root)) == 1


@pytest.mark.unit
@pytest.mark.usefixtures("_restore_root_logging")
def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    configure_logging()
    assert len(_stderr_handlers(logging.getLogger())) == 1
