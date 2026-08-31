"""Waiting on conditions, never on the clock.

`time.sleep(2)` is the single most common source of flaky UI automation: it is
simultaneously too long on a fast machine and too short on a loaded one. Every
wait here is a predicate with a deadline, and the failure message says what was
being waited for.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, TypeVar

from ..errors import AutomationError

log = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_TIMEOUT = 20.0
POLL_INTERVAL = 0.2


class WaitTimeout(AutomationError):
    """A condition did not become true before its deadline."""


def wait_for(
    predicate: Callable[[], Optional[T]],
    description: str,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> T:
    """Poll `predicate` until it returns something truthy, or time out.

    Exceptions raised by the predicate are treated as "not yet", which lets
    callers pass a lookup that legitimately fails while a window is still
    materialising.
    """
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
        except Exception as exc:  # noqa: BLE001 - a not-yet-ready UI throws freely
            last_error = exc
            result = None
        if result:
            return result
        time.sleep(interval)
    suffix = f" (last error: {last_error})" if last_error else ""
    raise WaitTimeout(f"timed out after {timeout:.0f}s waiting for {description}{suffix}")


def wait_until_stable(
    sample: Callable[[], object],
    description: str,
    quiet_period: float = 0.6,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> object:
    """Wait until `sample()` stops changing for `quiet_period` seconds.

    This is what "wait for the list to stabilize" means in the specification: a
    search result list repopulates asynchronously, and reading it while it is
    still settling produces a false "no match" — which would wrongly send the
    flow down the creation branch and duplicate a master record.
    """
    deadline = time.monotonic() + timeout
    previous = object()
    stable_since: Optional[float] = None
    while time.monotonic() < deadline:
        try:
            current = sample()
        except Exception:  # noqa: BLE001
            current = None
        now = time.monotonic()
        if current == previous:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= quiet_period:
                return current
        else:
            previous = current
            stable_since = None
        time.sleep(interval)
    raise WaitTimeout(f"timed out after {timeout:.0f}s waiting for {description} to stabilise")


def wait_gone(
    predicate: Callable[[], object],
    description: str,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> None:
    """Wait until something is no longer present — a closing dialog, usually."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not predicate():
                return
        except Exception:  # noqa: BLE001 - gone often means "lookup throws"
            return
        time.sleep(interval)
    raise WaitTimeout(f"timed out after {timeout:.0f}s waiting for {description} to disappear")
