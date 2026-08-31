"""Vision-LLM extraction — the primary path.

Why a vision model rather than zonal OCR: the automation must not assume the
source document's layout, for the same reason it must not assume Fakturama's.
A coordinate-keyed template would read the supplied image perfectly and fail on
the next one.

The model is constrained to a schema and asked only to transcribe. Every number
it returns is then re-derived and checked in `normalize.validate` — the model is
never trusted to do arithmetic.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path

from ..errors import ExtractionError
from ..models import OrderDocument
from .normalize import validate
from .schema import RawOrderExtraction, to_document

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"

SYSTEM_PROMPT = """\
You transcribe purchase-order documents into a fixed schema.

Rules:
- Transcribe only what is printed. Never infer, complete, or correct a value.
- Do not compute anything. Report totals exactly as they appear on the document,
  even if they look inconsistent — a downstream check re-derives them, and
  silently "fixing" a number would defeat that check.
- Copy SKUs, reference codes, and postal codes character for character.
- Keep numbers in the document's own notation; do not reformat separators.
- If the billing and delivery addresses are the same, repeat the billing address
  in both fields rather than leaving one blank.
- If a field genuinely is not present, return null. Do not invent placeholders.
"""

USER_PROMPT = (
    "Transcribe this order document into the schema. "
    "Include every line item, in the order printed."
)


def _image_block(image_path: Path) -> dict:
    media_type, _ = mimetypes.guess_type(image_path.name)
    if media_type not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        raise ExtractionError(
            f"unsupported image type {media_type!r} for {image_path.name}; "
            "expected png, jpeg, gif or webp"
        )
    data = base64.standard_b64encode(image_path.read_bytes()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


class LLMExtractor:
    """Extract an order document from an image using a vision model."""

    name = "llm"

    def __init__(self, model: str = MODEL, api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key

    def _client(self):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - import guard
            raise ExtractionError(
                "the 'anthropic' package is required for --extractor llm "
                "(pip install anthropic), or use --extractor ocr / --extractor fixture"
            ) from exc
        # A zero-arg client also resolves an `ant auth login` profile, so an unset
        # ANTHROPIC_API_KEY is not by itself a failure.
        return anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()

    def extract(self, image_path: Path) -> OrderDocument:
        client = self._client()
        log.info("extracting %s with %s", image_path.name, self.model)

        response = client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [_image_block(image_path), {"type": "text", "text": USER_PROMPT}],
                }
            ],
            output_format=RawOrderExtraction,
        )

        if response.stop_reason == "refusal":
            raise ExtractionError(f"model declined to transcribe the image: {response.stop_details}")

        raw = response.parsed_output
        if raw is None:
            raise ExtractionError("model returned no parsed output")

        log.debug("raw extraction: %s", raw.model_dump_json())
        return validate(to_document(raw))
