"""Fixture extraction: read a previously captured transcription from JSON.

Two uses. It lets the UI half of the flow be developed and re-run without
spending an API call on every iteration, and it makes the reconciliation logic
testable against known-bad inputs in CI.

It goes through exactly the same conversion and validation as the live path, so
a fixture cannot smuggle in a document that would not have been accepted.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..errors import ExtractionError
from ..models import OrderDocument
from .normalize import validate
from .schema import RawOrderExtraction, to_document

log = logging.getLogger(__name__)


class FixtureExtractor:
    """Load a transcription from a JSON file instead of reading the image."""

    name = "fixture"

    def __init__(self, fixture_path: Path) -> None:
        self.fixture_path = Path(fixture_path)

    def extract(self, image_path: Path) -> OrderDocument:  # noqa: ARG002 - interface
        if not self.fixture_path.is_file():
            raise ExtractionError(f"fixture not found: {self.fixture_path}")
        log.info("using fixture transcription %s", self.fixture_path)
        try:
            payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"fixture is not valid JSON: {exc}") from exc
        raw = RawOrderExtraction.model_validate(payload)
        return validate(to_document(raw))


def save_transcription(raw: RawOrderExtraction, path: Path) -> None:
    """Persist a transcription so a later run can replay it offline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw.model_dump_json(indent=2), encoding="utf-8")
    log.info("saved transcription to %s", path)
