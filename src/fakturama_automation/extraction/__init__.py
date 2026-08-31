"""Extraction backends.

All three implement the same one-method interface and all three end at
`normalize.validate`, so the flow layer cannot tell them apart and no backend can
hand it a document that does not reconcile.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..errors import ExtractionError
from ..models import OrderDocument


class Extractor(Protocol):
    name: str

    def extract(self, image_path: Path) -> OrderDocument: ...


def get_extractor(kind: str, *, fixture_path: Path | None = None, model: str | None = None) -> Extractor:
    """Build an extractor by name: 'llm', 'ocr' or 'fixture'."""
    kind = kind.lower()
    if kind == "llm":
        from .llm import MODEL, LLMExtractor

        return LLMExtractor(model=model or MODEL)
    if kind == "ocr":
        from .ocr import OCRExtractor

        return OCRExtractor()
    if kind == "fixture":
        if fixture_path is None:
            raise ExtractionError("--extractor fixture requires --fixture <path>")
        from .fixture import FixtureExtractor

        return FixtureExtractor(fixture_path)
    raise ExtractionError(f"unknown extractor {kind!r}; expected llm, ocr or fixture")


__all__ = ["Extractor", "get_extractor"]
