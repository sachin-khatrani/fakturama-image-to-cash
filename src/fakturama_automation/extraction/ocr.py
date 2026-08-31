"""OCR fallback extraction.

Exists so the pipeline is runnable and testable with no API key and no network.
It is a fallback, not the design — see DESIGN.md §3.

It reads the document by *label adjacency*: locate the printed label, then take
the value in the box below or to the right of it. That is the same idea the UI
grounding ladder uses for unnamed SWT fields, applied to pixels instead of a
widget tree — and it is the reason this is not a coordinate template. Nothing
here refers to an absolute position on the page.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..errors import ExtractionError
from ..models import OrderDocument
from .normalize import validate
from .schema import RawAddress, RawLineItem, RawOrderExtraction, to_document

log = logging.getLogger(__name__)

SKU_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
AMOUNT_PATTERN = re.compile(r"^-?[\d.,]+$")


@dataclass(frozen=True)
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def mid_y(self) -> int:
        return self.top + self.height // 2


@dataclass(frozen=True)
class Line:
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def top(self) -> int:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> int:
        return max(w.bottom for w in self.words)

    @property
    def left(self) -> int:
        return min(w.left for w in self.words)


class Page:
    """Word boxes grouped into lines, queryable by label adjacency."""

    def __init__(self, words: list[Word]) -> None:
        self.words = words
        self.lines = self._group(words)

    @staticmethod
    def _group(words: list[Word], tolerance: int = 8) -> list[Line]:
        rows: list[list[Word]] = []
        for word in sorted(words, key=lambda w: (w.mid_y, w.left)):
            for row in rows:
                if abs(row[0].mid_y - word.mid_y) <= max(tolerance, row[0].height // 2):
                    row.append(word)
                    break
            else:
                rows.append([word])
        return [
            Line(tuple(sorted(row, key=lambda w: w.left)))
            for row in sorted(rows, key=lambda r: r[0].top)
        ]

    def find_label(self, label: str) -> Optional[Line]:
        target = label.casefold()
        for line in self.lines:
            if target in line.text.casefold():
                return line
        return None

    def value_below(self, label: str, max_gap: int = 90) -> Optional[str]:
        """The first non-empty line starting below the label and roughly aligned to it."""
        anchor = self.find_label(label)
        if anchor is None:
            return None
        anchor_left = anchor.left
        for line in self.lines:
            if line.top <= anchor.bottom - 2:
                continue
            if line.top - anchor.bottom > max_gap:
                break
            if abs(line.left - anchor_left) > 60:
                continue
            return line.text.strip()
        return None

    def lines_below(self, label: str, count: int, max_gap: int = 200) -> list[str]:
        anchor = self.find_label(label)
        if anchor is None:
            return []
        out: list[str] = []
        previous_bottom = anchor.bottom
        for line in self.lines:
            if line.top <= anchor.bottom - 2:
                continue
            if line.top - previous_bottom > max_gap:
                break
            if abs(line.left - anchor.left) > 80:
                continue
            out.append(line.text.strip())
            previous_bottom = line.bottom
            if len(out) == count:
                break
        return out


def _read_page(image_path: Path) -> Page:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - import guard
        raise ExtractionError(
            "--extractor ocr needs pillow and pytesseract "
            "(pip install pillow pytesseract) plus the Tesseract binary on PATH"
        ) from exc

    cmd = os.environ.get("TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    try:
        data = pytesseract.image_to_data(
            Image.open(image_path), output_type=pytesseract.Output.DICT
        )
    except Exception as exc:  # pragma: no cover - depends on local install
        raise ExtractionError(
            f"Tesseract failed on {image_path.name}: {exc}. "
            "Install Tesseract and put it on PATH, or set TESSERACT_CMD."
        ) from exc

    words = [
        Word(text.strip(), data["left"][i], data["top"][i], data["width"][i], data["height"][i])
        for i, text in enumerate(data["text"])
        if text and text.strip()
    ]
    if not words:
        raise ExtractionError(f"OCR produced no text for {image_path.name}")
    return Page(words)


def _address_from_lines(lines: list[str]) -> RawAddress:
    """A 4-line block: name, street, '<zip> <city>', country."""
    if len(lines) < 4:
        raise ExtractionError(f"could not read a complete address block, got {lines!r}")
    name, street, zip_city, country = lines[0], lines[1], lines[2], lines[3]
    match = re.match(r"^\s*(\d{4,6})\s+(.*)$", zip_city)
    if not match:
        raise ExtractionError(f"could not split ZIP and city from {zip_city!r}")
    return RawAddress(
        name=name, street=street, zip=match.group(1), city=match.group(2), country=country
    )


def _items_from_page(page: Page) -> list[RawLineItem]:
    """Rows are found by their SKU token, then read across the same line."""
    items: list[RawLineItem] = []
    for line in page.lines:
        skus = [w for w in line.words if SKU_PATTERN.match(w.text)]
        if not skus:
            continue
        sku_word = skus[0]
        before = [w.text for w in line.words if w.right <= sku_word.left]
        after = [w for w in line.words if w.left >= sku_word.right]
        numbers = [w.text for w in after if AMOUNT_PATTERN.match(w.text) or w.text.endswith("%")]
        description = " ".join(
            w.text
            for w in after
            if not AMOUNT_PATTERN.match(w.text) and not w.text.endswith("%") and w.text != "pcs"
        )
        # qty, unit net, discount%, vat%, line net — in printed column order.
        if len(numbers) < 5:
            log.debug("skipping line, too few numeric columns: %s", line.text)
            continue
        qty, unit_net, discount, vat, line_net = numbers[:5]
        position = int(before[0]) if before and before[0].isdigit() else len(items) + 1
        items.append(
            RawLineItem(
                position=position,
                sku=sku_word.text,
                description=description.strip(),
                quantity=qty,
                unit_net_price=unit_net,
                discount_percent=discount.rstrip("%"),
                vat_percent=vat.rstrip("%"),
                line_net=line_net,
            )
        )
    if not items:
        raise ExtractionError("OCR found no item rows")
    return items


def _require(value: Optional[str], field: str) -> str:
    if not value:
        raise ExtractionError(f"OCR could not read a value for {field!r}")
    return value


class OCRExtractor:
    """Label-adjacency OCR extraction. Fallback for offline runs."""

    name = "ocr"

    def extract(self, image_path: Path) -> OrderDocument:
        page = _read_page(image_path)
        log.info("OCR read %d lines from %s", len(page.lines), image_path.name)

        contact = _require(page.value_below("CONTACT NAME"), "contact name")
        parts = contact.split()
        totals_line = page.find_label("NET TOTAL")

        raw = RawOrderExtraction(
            external_reference=_require(page.value_below("EXTERNAL REFERENCE"), "external reference"),
            order_date=_require(page.value_below("ORDER DATE"), "order date"),
            currency=(page.value_below("CURRENCY") or "EUR"),
            company=_require(page.value_below("COMPANY"), "company"),
            contact_first_name=" ".join(parts[:-1]) if len(parts) > 1 else contact,
            contact_last_name=parts[-1] if len(parts) > 1 else "",
            customer_alias=page.value_below("CUSTOMER ALIAS"),
            customer_id=page.value_below("CUSTOMER ID"),
            email=page.value_below("EMAIL"),
            phone=page.value_below("PHONE"),
            billing_address=_address_from_lines(page.lines_below("BILLING ADDRESS", 4)),
            delivery_address=_address_from_lines(page.lines_below("DELIVERY ADDRESS", 4)),
            payment_method=_require(page.value_below("PAYMENT METHOD"), "payment method"),
            paid_status=_require(page.value_below("PAID STATUS"), "paid status"),
            payment_date=page.value_below("PAYMENT DATE"),
            items=_items_from_page(page),
            net_total=_require(page.value_below("NET TOTAL"), "net total"),
            vat_total=_require(page.value_below("VAT TOTAL"), "VAT total"),
            gross_total=_require(page.value_below("GROSS TOTAL"), "gross total"),
        )
        if totals_line is None:
            log.warning("totals band not located by label; values may be unreliable")
        return validate(to_document(raw))
