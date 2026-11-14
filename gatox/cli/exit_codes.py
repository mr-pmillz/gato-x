"""Stable process statuses for shell scripts and container callers."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import IntEnum


class ExitCode(IntEnum):
    """Distinguish completed commands, failures, usage errors and empty results."""

    SUCCESS = 0
    ERROR = 1
    USAGE = 2
    NO_RESULTS = 3
    INTERRUPTED = 130


@dataclass
class CommandDiagnostics:
    """Track reported failures, including legacy commands without return values."""

    failed: bool = False


_diagnostics: ContextVar[CommandDiagnostics | None] = ContextVar(
    "command_diagnostics", default=None
)


@contextmanager
def command_diagnostics() -> Iterator[CommandDiagnostics]:
    """Collect failures within one command and its child tasks, then reset."""
    diagnostics = CommandDiagnostics()
    token = _diagnostics.set(diagnostics)
    try:
        yield diagnostics
    finally:
        _diagnostics.reset(token)


def record_error() -> None:
    """Mark the active command failed even when its output is suppressed."""
    diagnostics = _diagnostics.get()
    if diagnostics is not None:
        diagnostics.failed = True
