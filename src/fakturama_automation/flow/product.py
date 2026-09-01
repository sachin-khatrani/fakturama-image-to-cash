"""Step 3 — select or create each Product, its VAT rate, and complete the line.

Ordering matters here and is not cosmetic: the VAT record must exist *before*
New product is opened, because the Product editor reads the VAT list when it
opens. Creating the VAT afterwards leaves the dropdown without the entry and the
product is saved against the wrong rate.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from ..errors import ManualReviewRequired
from ..models import LineItem
from ..uia.backend import Session
from . import ui
from .debtor import open_data_menu
from .selectors import Row, resolve_or_create
from .grid import ItemGrid

log = logging.getLogger(__name__)

STEP = "3-product"
STEP_VAT = "3.4-vat"


def process_items(session: Session, items: list[LineItem]) -> ItemGrid:
    """Spec 3.1 — run the whole branch for every item, in source order.

    Returns the grid so step 4.1 can re-read the lines before the Order is saved.
    """
    grid = ItemGrid(session)
    for item in items:
        log.info("%s: line %d — %s", STEP, item.position, item.sku)
        select_or_create_product(session, item)
        complete_line(session, grid, item)
    return grid


def select_or_create_product(session: Session, item: LineItem) -> None:
    """Spec 3.2–3.12."""
    session.click(ui.PRODUCT_SELECT_ICON)  # 3.2 — upper icon, not the green +

    resolve_or_create(
        session,
        step=f"{STEP}-{item.sku}",
        dialog_title=ui.PRODUCT_DIALOG_TITLE,
        search_term=item.sku,
        is_exact=lambda row: _is_exact_product(row, item),
        create=lambda: _create_product(session, item),
        entity="Product",
    )


def _is_exact_product(row: Row, item: LineItem) -> bool:
    """Spec 3.3 — exact SKU only.

    Substring containment is not enough on its own: 'CHR-ERG-01' is contained in
    'CHR-ERG-011'. The SKU must appear as a whole field or a whole token.
    """
    sku = item.sku.strip().casefold()
    if any(cell.strip().casefold() == sku for cell in row.cells):
        return True
    tokens = row.text.replace("|", " ").split()
    return any(token.strip().casefold() == sku for token in tokens)


# --------------------------------------------------------------------------- #
# creation branch
# --------------------------------------------------------------------------- #


def _create_product(session: Session, item: LineItem) -> None:
    """Spec 3.4–3.11."""
    _ensure_vat(session, item)  # 3.4–3.6 — before New product, deliberately

    session.click(ui.NEW_PRODUCT)  # 3.7
    session.wait_for_window("product")
    session.invalidate()

    session.set_text(ui.PRODUCT_ITEM_NUMBER, item.sku)  # 3.8
    session.set_text(ui.PRODUCT_NAME, item.description)
    session.set_text(ui.PRODUCT_DESCRIPTION, item.description)

    # 3.9 — gross = unit net x (1 + VAT/100), 2dp. The line discount is NOT
    # applied: it belongs to this transaction, not to the product master record.
    session.set_text(ui.PRODUCT_PRICE_GROSS, _decimal_text(item.product_gross_price))

    session.set_text(ui.PRODUCT_COST_PRICE, "0.00")  # 3.10
    session.select_option(ui.PRODUCT_VAT, item.vat_name)
    session.set_text(ui.PRODUCT_STOCK, "0.00")
    # Category, GTIN, supplier code, allowance, picture and user field 1 are
    # deliberately left untouched.

    session.shot(f"{STEP}-{item.sku}-before-save")
    session.click(ui.TOOLBAR_SAVE)  # 3.11 — once
    log.info(
        "%s: created product %s at gross %s with %s",
        STEP,
        item.sku,
        item.product_gross_price,
        item.vat_name,
    )


def _ensure_vat(session: Session, item: LineItem) -> None:
    """Spec 3.4–3.6 — reuse an exact VAT record, or create one.

    Reuse requires all three of name, value and E-Invoice code to agree. A row
    named 'VAT 19%' whose value is 7 would quietly mis-tax every line booked
    against it, so a conflict stops the run instead of being adopted.
    """
    open_data_menu(session, ui.MENU_DATA_VATS)
    rows = _search_vat(session, item.vat_name)
    candidates = [row for row in rows if row.contains_all([item.vat_name])]

    if len(candidates) > 1:
        shot = session.shot(f"{STEP_VAT}-ambiguous")
        raise ManualReviewRequired(
            STEP_VAT,
            f"{len(candidates)} VAT rows match {item.vat_name!r}",
            observed=[row.text for row in candidates],
            screenshot=str(shot) if shot else None,
        )

    if len(candidates) == 1:
        _confirm_existing_vat(session, candidates[0], item)  # 3.5
        return

    session.click(ui.LIST_NEW_ICON)  # 3.6
    session.invalidate()
    session.set_text(ui.VAT_NAME, item.vat_name)
    session.set_text(ui.VAT_DESCRIPTION, item.vat_name)
    session.select_option(ui.VAT_CODE, ui.VAT_STANDARD_RATE_CODE)
    session.set_text(ui.VAT_VALUE, _decimal_text(item.vat_percent))
    # 'Standard VAT' is left exactly as displayed.

    session.shot(f"{STEP_VAT}-before-save")
    session.click(ui.TOOLBAR_SAVE)
    log.info("%s: created %s at %s%%", STEP_VAT, item.vat_name, item.vat_percent)


def _confirm_existing_vat(session: Session, row: Row, item: LineItem) -> None:
    """Spec 3.5 — reuse a VAT record only when all three settings agree.

    Name, Value AND the E-Invoice code must match. The list view does not
    reliably show the code column, so the record is opened and read: a row named
    'VAT 19%' whose code is a reduced or zero rate would mis-tax every line
    booked against it, and that is exactly the conflict this step exists to
    catch. Reusing on name alone would defeat the check entirely.
    """
    try:
        row.control.DoubleClick(simulateMove=False)
        session.wait_for_window("VAT")
        session.invalidate()
    except Exception as exc:  # noqa: BLE001
        shot = session.shot(f"{STEP_VAT}-unreadable")
        raise ManualReviewRequired(
            STEP_VAT,
            f"an existing VAT row matches {item.vat_name!r} but could not be opened to "
            "confirm its value and E-Invoice code",
            observed=f"{row.text} ({exc})",
            screenshot=str(shot) if shot else None,
        ) from exc

    problems = []
    checks = (
        ("Name", ui.VAT_NAME, item.vat_name),
        ("Value", ui.VAT_VALUE, _decimal_text(item.vat_percent)),
        ("VAT code (E-Invoice)", ui.VAT_CODE, ui.VAT_STANDARD_RATE_CODE),
    )
    for label, locator, expected in checks:
        try:
            actual = session.get_text(locator).strip()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label}: could not read ({exc})")
            continue
        if not _setting_matches(label, actual, expected):
            problems.append(f"{label}: expected {expected!r}, found {actual!r}")

    if problems:
        shot = session.shot(f"{STEP_VAT}-conflict", highlight=ui.VAT_CODE)
        raise ManualReviewRequired(
            STEP_VAT,
            f"the existing {item.vat_name!r} record conflicts with what this order needs",
            expected=f"name={item.vat_name}, value={item.vat_percent}, code={ui.VAT_STANDARD_RATE_CODE}",
            observed=problems,
            screenshot=str(shot) if shot else None,
        )
    log.info("%s: reusing existing %s (value and standard-rate code confirmed)", STEP_VAT, item.vat_name)


def _setting_matches(label: str, actual: str, expected: str) -> bool:
    a, b = actual.casefold(), expected.casefold()
    if label.startswith("VAT code"):
        # 'S (Standard rate)' may render as 'S', 'Standard rate' or both.
        return a == b or a.startswith("s") or "standard" in a
    if b in a or a in b:
        return True
    a_digits = "".join(ch for ch in a if ch.isdigit())
    b_digits = "".join(ch for ch in b if ch.isdigit())
    return bool(b_digits) and (a_digits == b_digits or a_digits.lstrip("0") == b_digits.lstrip("0"))


def _search_vat(session: Session, name: str) -> list[Row]:
    from ..uia.waits import wait_until_stable

    from .selectors import read_rows

    try:
        session.set_text(ui.LIST_SEARCH, name, verify=False)
    except Exception:  # noqa: BLE001
        pass

    def rows() -> list[Row]:
        table = session.resolver.try_resolve(ui.DOCUMENTS_TABLE)
        return read_rows(table) if table is not None else []

    wait_until_stable(lambda: tuple(r.text for r in rows()), f"VAT list for {name!r}")
    return rows()


# --------------------------------------------------------------------------- #
# line completion
# --------------------------------------------------------------------------- #


def complete_line(session: Session, grid: ItemGrid, item: LineItem) -> None:
    """Spec 3.13–3.16."""
    row_index = item.position - 1
    grid.set_cell(row_index, "Qty.", _decimal_text(item.quantity))  # 3.13
    grid.set_cell(row_index, "U.Price", _decimal_text(item.unit_net_price))  # 3.14
    grid.set_cell(row_index, "VAT", _decimal_text(item.vat_percent))
    grid.set_cell(row_index, "Discount", _decimal_text(item.discount_percent))  # 3.15

    # 3.16 — confirm the line price Fakturama computed matches ours.
    expected = item.computed_line_net
    actual_text = grid.get_cell(row_index, "Price")
    if not _amount_matches(actual_text, expected):
        shot = session.shot(f"{STEP}-{item.sku}-line-price-mismatch")
        raise ManualReviewRequired(
            f"{STEP}-{item.sku}",
            "line price does not match quantity x unit net x (1 - discount/100)",
            expected=str(expected),
            observed=actual_text,
            screenshot=str(shot) if shot else None,
        )
    log.info("%s: line %d confirmed at %s", STEP, item.position, expected)


def _amount_matches(text: str, expected: Decimal) -> bool:
    if text is None:
        return False
    digits = "".join(ch for ch in text if ch.isdigit())
    want = "".join(ch for ch in f"{expected:.2f}" if ch.isdigit())
    return digits == want or digits.lstrip("0") == want.lstrip("0")


def _decimal_text(value: Decimal) -> str:
    """Render a Decimal the way a person would type it into a form."""
    quantised = value.quantize(Decimal("0.01"))
    if quantised == quantised.to_integral_value():
        return str(int(quantised))
    return f"{quantised:.2f}"
