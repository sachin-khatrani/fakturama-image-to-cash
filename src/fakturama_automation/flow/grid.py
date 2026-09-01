"""Driving the Order's Items table.

This is the risk the design document names up front. Eclipse RCP applications
often render document line-item grids on a canvas, which publishes no per-cell
UIA elements — so the strategies that work for the rest of the form return
nothing here, and a driver written on the assumption of real cells fails on the
first line.

Rather than assume either shape, the grid is probed once at construction and the
strategy is chosen from what is actually there:

  ELEMENT mode  cells are real elements — set and read them through their own
                patterns, exactly like any other field.

  CANVAS mode   the grid is one drawn surface — navigate with the keyboard from
                a known anchor cell and read values back by OCR-ing the *cell's
                own rectangle*. Still no absolute coordinates: the rectangle
                comes from the element that owns it.

Verification survives both. In canvas mode, if OCR is unavailable the run stops
for manual review rather than continuing unverified — an unverified write to an
accounting document is worse than a halt.
"""

from __future__ import annotations

import logging
from typing import Optional

import uiautomation as auto

from ..errors import ManualReviewRequired
from ..uia.backend import Session
from ..uia.locators import Locator, _rect, _type_name, descendants

log = logging.getLogger(__name__)

STEP = "3-items-grid"

# Printed column order of the Order's item table, used for keyboard navigation
# in canvas mode. Confirmed against the running application by the inspector;
# it is a fallback for a grid that exposes no headers of its own.
# Confirmed against Fakturama 2.2.0's Order editor. Note 'Item No.' (not
# 'Item Number'), the 'Picture' column between Item No. and Name, and VAT sitting
# BEFORE U.Price -- a keyboard-driven grid walks these by position, so the order
# is load-bearing, not documentation.
DEFAULT_COLUMNS = (
    "Pos.", "Qty.", "Item No.", "Picture", "Name", "Description", "VAT", "U.Price", "Discount", "Price",
)

ITEMS_TABLE_CANDIDATES = (
    Locator("Items table", control_type="Table"),
    Locator("Items table (DataGrid)", control_type="DataGrid"),
    Locator("Items table (List)", control_type="List"),
    Locator("Items table (Custom canvas)", control_type="Custom"),
)


class ItemGrid:
    """A strategy-selecting facade over the Order's item table."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.table = self._locate_table()
        self.columns = self._read_columns()
        self.mode = self._detect_mode()
        log.info(
            "%s: driving the items table in %s mode with columns %s",
            STEP,
            self.mode,
            list(self.columns),
        )

    # ------------------------------------------------------------------ probing

    def _locate_table(self) -> auto.Control:
        for locator in ITEMS_TABLE_CANDIDATES:
            control = self.session.resolver.try_resolve(locator)
            if control is None:
                continue
            rect = _rect(control)
            if rect is not None and rect.width() > 300 and rect.height() > 60:
                return control
        raise ManualReviewRequired(
            STEP,
            "could not locate the Order's Items table; run the inspector "
            "(python -m fakturama_automation.uia.inspector --probe-grid) and "
            "correct ITEMS_TABLE_CANDIDATES",
        )

    def _detect_mode(self) -> str:
        rows = self._element_rows()
        if rows:
            return "element"
        for getter in ("GetGridPattern", "GetTablePattern"):
            accessor = getattr(self.table, getter, None)
            try:
                if accessor is not None and accessor() is not None:
                    return "element"
            except Exception:  # noqa: BLE001
                continue
        log.warning(
            "%s: the items table exposes no cell elements — falling back to "
            "keyboard navigation with OCR read-back",
            STEP,
        )
        return "canvas"

    def _element_rows(self) -> list[auto.Control]:
        return [
            control
            for control in descendants(self.table, 3)
            if _type_name(control) in ("DataItem", "ListItem", "TreeItem")
        ]

    def _read_columns(self) -> tuple[str, ...]:
        headers = [
            (control.Name or "").strip()
            for control in descendants(self.table, 3)
            if _type_name(control) in ("Header", "HeaderItem") and (control.Name or "").strip()
        ]
        return tuple(headers) if headers else DEFAULT_COLUMNS

    def _column_index(self, column: str) -> int:
        wanted = column.strip().casefold().rstrip(".")
        for index, name in enumerate(self.columns):
            if name.strip().casefold().rstrip(".") == wanted:
                return index
        raise ManualReviewRequired(
            STEP,
            f"the items table has no column named {column!r}",
            observed=list(self.columns),
        )

    # ------------------------------------------------------------------- access

    def set_cell(self, row: int, column: str, value: str) -> None:
        if self.mode == "element":
            self._set_cell_element(row, column, value)
        else:
            self._set_cell_canvas(row, column, value)

    def get_cell(self, row: int, column: str) -> str:
        if self.mode == "element":
            return self._get_cell_element(row, column)
        return self._get_cell_canvas(row, column)

    # ------------------------------------------------------------ element mode

    def _cell_element(self, row: int, column: str) -> Optional[auto.Control]:
        rows = self._element_rows()
        if row >= len(rows):
            return None
        cells = [
            control
            for control in descendants(rows[row], 2)
            if _type_name(control) in ("Edit", "Text", "DataItem", "ComboBox")
        ]
        index = self._column_index(column)
        return cells[index] if index < len(cells) else None

    def _set_cell_element(self, row: int, column: str, value: str) -> None:
        cell = self._cell_element(row, column)
        if cell is None:
            raise ManualReviewRequired(STEP, f"no cell at row {row}, column {column!r}")
        if not Session._set_via_value_pattern(cell, value):
            cell.SetFocus()
            auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.03)
            auto.SendKeys(value.replace("{", "{{}").replace("}", "{}}"), waitTime=0.01)
            auto.SendKeys("{Tab}", waitTime=0.05)
        actual = Session.read(cell).strip()
        if not _digits_equal(actual, value):
            raise ManualReviewRequired(
                STEP,
                f"cell {column!r} on row {row + 1} did not accept the value",
                expected=value,
                observed=actual,
            )

    def _get_cell_element(self, row: int, column: str) -> str:
        cell = self._cell_element(row, column)
        return Session.read(cell).strip() if cell is not None else ""

    # ------------------------------------------------------------- canvas mode

    def _focus_cell_canvas(self, row: int, column: str) -> None:
        """Walk the keyboard graph to a cell from the grid's own origin.

        Home/Ctrl+Home returns to the first cell, then Down and Tab step to the
        target. This is rung 5 of the ladder: a route through the widget's own
        keyboard model, not a position on the screen.
        """
        self.table.SetFocus()
        auto.SendKeys("{Ctrl}{Home}", waitTime=0.05)
        for _ in range(row):
            auto.SendKeys("{Down}", waitTime=0.03)
        for _ in range(self._column_index(column)):
            auto.SendKeys("{Tab}", waitTime=0.03)

    def _set_cell_canvas(self, row: int, column: str, value: str) -> None:
        self._focus_cell_canvas(row, column)
        auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.03)
        auto.SendKeys(value.replace("{", "{{}").replace("}", "{}}"), waitTime=0.01)
        auto.SendKeys("{Enter}", waitTime=0.08)
        actual = self._get_cell_canvas(row, column)
        if not _digits_equal(actual, value):
            raise ManualReviewRequired(
                STEP,
                f"cell {column!r} on row {row + 1} did not read back as written",
                expected=value,
                observed=actual,
            )

    def _get_cell_canvas(self, row: int, column: str) -> str:
        """OCR the focused cell's own rectangle."""
        focused = self._focused_element()
        rect = _rect(focused) if focused is not None else None
        if rect is None:
            raise ManualReviewRequired(
                STEP, f"could not determine the rectangle of cell {column!r} on row {row + 1}"
            )
        return _ocr_rect(rect)

    @staticmethod
    def _focused_element() -> Optional[auto.Control]:
        try:
            return auto.GetFocusedControl()
        except Exception:  # noqa: BLE001
            return None


def _digits_equal(actual: str, expected: str) -> bool:
    a = "".join(ch for ch in actual if ch.isdigit())
    b = "".join(ch for ch in expected if ch.isdigit())
    if not b:
        return True
    return a == b or a.rstrip("0") == b.rstrip("0")


def _ocr_rect(rect) -> str:
    """Read text out of a screen rectangle belonging to a specific element."""
    try:
        import pytesseract
        from PIL import ImageGrab
    except ImportError as exc:  # pragma: no cover - import guard
        raise ManualReviewRequired(
            STEP,
            "the items table is canvas-rendered, so cell values can only be "
            "verified by OCR, but pillow/pytesseract are not installed. "
            "Install them (and the Tesseract binary) or drive this Fakturama "
            "build in element mode — continuing would write item lines without "
            "verifying them",
        ) from exc
    image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
    return pytesseract.image_to_string(image, config="--psm 7").strip()
