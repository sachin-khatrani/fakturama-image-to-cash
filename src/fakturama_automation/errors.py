"""Failure taxonomy.

The distinction that matters: `ManualReviewRequired` means the automation
behaved correctly and is refusing to guess. Everything else is a defect.
Callers exit with different codes for the two, so a supervising process can
tell "needs a human" apart from "needs a fix".
"""

from __future__ import annotations

from typing import Optional


class AutomationError(Exception):
    """Base for every error this package raises."""


class ExtractionError(AutomationError):
    """The source image could not be read into a document that adds up."""


class ControlNotFound(AutomationError):
    """A locator exhausted every strategy in the resolution ladder."""

    def __init__(self, description: str, tried: Optional[list[str]] = None) -> None:
        self.description = description
        self.tried = tried or []
        detail = f"; tried: {', '.join(self.tried)}" if self.tried else ""
        super().__init__(f"could not resolve control: {description}{detail}")


class VerificationError(AutomationError):
    """A value was written but did not read back as expected.

    This is always a hard failure: it means the application did not accept what
    the automation believes it entered.
    """

    def __init__(self, field: str, expected: object, actual: object) -> None:
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__(f"{field}: expected {expected!r}, found {actual!r}")


class ManualReviewRequired(AutomationError):
    """A human must decide. Raised on every ambiguity the specification calls out.

    Carries enough context to act on without re-running: which step, what was
    expected, what was actually seen.
    """

    def __init__(
        self,
        step: str,
        reason: str,
        expected: object = None,
        observed: object = None,
        screenshot: Optional[str] = None,
    ) -> None:
        self.step = step
        self.reason = reason
        self.expected = expected
        self.observed = observed
        self.screenshot = screenshot
        parts = [f"[{step}] {reason}"]
        if expected is not None:
            parts.append(f"expected={expected!r}")
        if observed is not None:
            parts.append(f"observed={observed!r}")
        if screenshot:
            parts.append(f"screenshot={screenshot}")
        super().__init__(" | ".join(parts))


class DuplicateDocument(ManualReviewRequired):
    """The external reference is already booked. Refuse rather than double-book."""
