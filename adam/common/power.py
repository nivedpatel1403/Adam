"""Temporary Windows idle-sleep inhibition during an analysis run.

Requests belong to the calling thread, never change the saved power plan,
and cannot override a user closing the lid or explicitly requesting sleep.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import ParamSpec, TypeVar

_WINDOWS = sys.platform == "win32"
_CONTINUOUS = 0x80000000
_SYSTEM_REQUIRED = 0x00000001
_state = threading.local()
logger = logging.getLogger(__name__)
P = ParamSpec("P")
T = TypeVar("T")


def _execution_state(flags: int) -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    request = kernel.SetThreadExecutionState
    request.argtypes = [ctypes.c_uint]
    request.restype = ctypes.c_uint
    result = request(flags)
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


@contextmanager
def system_awake() -> Iterator[None]:
    """Keep idle sleep from interrupting work; nested async runs share a request."""
    if not _WINDOWS:
        yield
        return
    depth = getattr(_state, "depth", 0)
    if depth == 0:
        _state.previous = _execution_state(_CONTINUOUS | _SYSTEM_REQUIRED)
        logger.info("Temporary Windows system-awake request acquired")
    _state.depth = depth + 1
    try:
        yield
    finally:
        _state.depth -= 1
        if _state.depth == 0:
            _execution_state(_state.previous)
            logger.info("Temporary Windows system-awake request released")


def keep_system_awake(function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with system_awake():
            return await function(*args, **kwargs)
    return wrapped
