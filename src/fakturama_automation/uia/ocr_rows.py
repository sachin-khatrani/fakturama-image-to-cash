"""Reading rows out of a canvas-rendered list.

Fakturama draws every data view on a canvas — the item grid, the Documents list,
the VAT and payment lists, and the result lists inside the selector dialogs. None
of them publish row or cell elements, so UI Automation has nothing to return and
`read_rows` comes back empty whether the list is full or genuinely empty.

That ambiguity is dangerous rather than merely inconvenient: "no match" and
"could not see" lead to opposite decisions, and taking the create branch on a
blind read duplicates master data on every run.

So rows are recovered by OCR of the control's **own rectangle** — the rectangle
UI Automation reports for that element, not a fixed screen region. The capture
therefore follows the control when the window moves, resizes or changes DPI, and
no coordinate is ever hardcoded.

Each visual text line becomes one row. Columns are not reconstructed: the flow's
matching only ever asks "does this row contain all of these values", which a flat
line answers correctly.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import uiautomation as auto

from .locators import _rect

log = logging.getLogger(__name__)

# Rows shorter than this are chrome — column headers, scrollbar labels, noise.
MIN_ROW_CHARS = 3


def ocr_available() -> bool:
    """Whether OCR row reading can be used at all."""
    try:
        import pytesseract  # noqa: F401
        from PIL import ImageGrab  # noqa: F401
    except ImportError:
        return False
    return _configure() is not None


def _configure() -> Optional[str]:
    """Point pytesseract at the binary, honouring TESSERACT_CMD."""
    try:
        import pytesseract
    except ImportError:
        return None
    command = os.environ.get("TESSERACT_CMD")
    if command:
        pytesseract.pytesseract.tesseract_cmd = command
    try:
        pytesseract.get_tesseract_version()
    except Exception:  # noqa: BLE001 - binary missing or not executable
        return None
    return pytesseract.pytesseract.tesseract_cmd


def read_text_rows(control: auto.Control) -> list[str]:
    """OCR a control's rectangle and return one string per visual line."""
    if not ocr_available():
        return []
    rect = _rect(control)
    if rect is None or rect.width() < 40 or rect.height() < 20:
        return []

    import pytesseract
    from PIL import ImageGrab

    try:
        image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
    except Exception as exc:  # noqa: BLE001 - screen capture can fail transiently
        log.debug("could not capture the list rectangle: %s", exc)
        return []

    try:
        # --psm 6 reads a uniform block line by line, which is what a table is.
        data = pytesseract.image_to_data(
            image, config="--psm 6", output_type=pytesseract.Output.DICT
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("OCR of the list failed: %s", exc)
        return []

    lines: dict[tuple[int, int, int], list[tuple[int, str]]] = {}
    for index, text in enumerate(data["text"]):
        if not text or not text.strip():
            continue
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        lines.setdefault(key, []).append((data["left"][index], text.strip()))

    rows: list[str] = []
    for key in sorted(lines, key=lambda k: (k[0], k[1], k[2])):
        words = [text for _, text in sorted(lines[key])]
        joined = " ".join(words).strip()
        if len(joined) >= MIN_ROW_CHARS:
            rows.append(joined)
    log.debug("OCR recovered %d row(s) from the list", len(rows))
    return rows
