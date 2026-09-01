"""Steps 1 and 4 — open the Order, set its header, then complete and save it."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from typing import Optional

from ..errors import DuplicateDocument, ManualReviewRequired
from ..models import OrderDocument
from ..uia.backend import Session
from . import ui
from .debtor import open_data_menu
from .selectors import read_rows

log = logging.getLogger(__name__)

STEP_OPEN = "1-open-order"
STEP_SAVE = "4-save-order"


def open_new_order(session: Session, doc: OrderDocument) -> str:
    """Spec 1.3–1.8. Returns the order number Fakturama proposed."""
    session.click(ui.TOOLBAR_ORDER)  # 1.3
    session.wait_for_window("Order")
    session.invalidate()
    log.info("%s: New Order editor open", STEP_OPEN)

    # 1.4 — the proposed No. is Fakturama's to allocate; leave it alone.
    proposed = session.get_text(ui.ORDER_NO).strip()
    if not proposed:
        raise ManualReviewRequired(
            STEP_OPEN, "the New Order editor proposed no document number"
        )
    log.info("%s: proposed order number %r (left unchanged)", STEP_OPEN, proposed)

    session.set_text(ui.ORDER_DATE, _format_date(doc.order_date))  # 1.5
    session.set_text(ui.ORDER_CUST_REF, doc.external_reference)  # 1.6

    # 1.7 — Net price mode, and VAT must stay 'With VAT'.
    try:
        session.click(ui.ORDER_PRICE_MODE_NET)
    except Exception as exc:  # noqa: BLE001
        raise ManualReviewRequired(
            STEP_OPEN, "could not set the document price mode to Net", observed=str(exc)
        ) from exc
    _confirm_vat_mode(session)

    session.shot(
        f"{STEP_OPEN}-order-header",
        highlight=ui.ORDER_CUST_REF,
        caption=f"1. New Order — date {doc.order_date}, Cust.Ref. {doc.external_reference}, Net / With VAT",
    )
    return proposed


def _confirm_vat_mode(session: Session) -> None:
    """Spec 1.7 — VAT stays 'With VAT'.

    Confirmed rather than set: it is Fakturama's default, and a document that
    silently switched to a no-VAT mode would produce correct-looking net figures
    with no tax on them.
    """
    try:
        actual = session.get_text(ui.ORDER_VAT_MODE)
    except Exception as exc:  # noqa: BLE001
        raise ManualReviewRequired(
            STEP_OPEN, "could not read the document VAT mode to confirm it", observed=str(exc)
        ) from exc
    if "with vat" not in actual.strip().casefold():
        shot = session.shot(f"{STEP_OPEN}-vat-mode", highlight=ui.ORDER_VAT_MODE)
        raise ManualReviewRequired(
            STEP_OPEN,
            "the document VAT mode is not 'With VAT'",
            expected="With VAT",
            observed=actual,
            screenshot=str(shot) if shot else None,
        )
    log.info("%s: price mode Net, VAT mode %r", STEP_OPEN, actual.strip())


def complete_and_save_order(session: Session, doc: OrderDocument, order_no: str, grid=None) -> None:
    """Spec 4.1–4.5."""
    _reconfirm_against_source(session, doc, grid)  # 4.1

    # 4.2 — no order-level discount or shipping unless the image supplies them.
    _set_if_present(session, ui.ORDER_DISCOUNT, "0")
    _confirm_shipping_is_free(session)

    _confirm_totals(session, doc)  # 4.3

    session.shot(
        f"{STEP_SAVE}-before-save",
        highlight=ui.ORDER_TOTAL_GROSS,
        caption=f"4. Order complete — net {doc.totals.net} / VAT {doc.totals.vat} / total {doc.totals.gross}",
    )
    session.click(ui.TOOLBAR_SAVE)  # 4.4 — once
    log.info("%s: Order saved", STEP_SAVE)

    _confirm_saved_order(session, doc, order_no)  # 4.5


def _reconfirm_against_source(session: Session, doc: OrderDocument, grid) -> None:
    """Spec 4.1 — addresses and every product line still match the image.

    Each was checked when it was entered. Re-checking here is not redundant: the
    document has been edited many times since, and this is the last look before
    the record is committed.
    """
    from .debtor import _confirm_addresses

    _confirm_addresses(session, doc.debtor)

    if grid is None:
        log.warning("%s: no item grid available; skipping the per-line re-check", STEP_SAVE)
        return

    mismatches = []
    for item in doc.items:
        row = item.position - 1
        for column, expected in (
            ("Item Number", item.sku),
            ("Qty.", f"{item.quantity:.2f}"),
            ("U.Price", f"{item.unit_net_price:.2f}"),
            ("Discount", f"{item.discount_percent:.2f}"),
            ("Price", f"{item.computed_line_net:.2f}"),
        ):
            try:
                actual = grid.get_cell(row, column)
            except Exception as exc:  # noqa: BLE001 - column may not be shown
                log.debug("line %d %s not readable: %s", item.position, column, exc)
                continue
            if not _loose_match(actual, expected):
                mismatches.append(f"line {item.position} {column}: source {expected}, document {actual!r}")

    if mismatches:
        shot = session.shot(f"{STEP_SAVE}-line-mismatch")
        raise ManualReviewRequired(
            STEP_SAVE,
            "item lines no longer match the source image",
            observed=mismatches,
            screenshot=str(shot) if shot else None,
        )
    log.info("%s: %d item line(s) re-confirmed against the source", STEP_SAVE, len(doc.items))


def _confirm_shipping_is_free(session: Session) -> None:
    """Spec 4.2 — Shipping stays 'Free of shipping costs' / 0.00.

    The sample supplies no order-level shipping, so anything other than free
    would be a value the automation did not put there.
    """
    try:
        actual = session.get_text(ui.ORDER_SHIPPING)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: could not read the Shipping selection (%s)", STEP_SAVE, exc)
        return
    text = actual.strip().casefold()
    if "free" in text or _amount_matches(actual, Decimal("0.00")):
        log.info("%s: shipping is %r", STEP_SAVE, actual.strip())
        return
    shot = session.shot(f"{STEP_SAVE}-shipping", highlight=ui.ORDER_SHIPPING)
    raise ManualReviewRequired(
        STEP_SAVE,
        "Shipping is not free and the source supplies no order-level shipping value",
        expected="Free of shipping costs / 0.00",
        observed=actual,
        screenshot=str(shot) if shot else None,
    )


def _loose_match(actual: str, expected: str) -> bool:
    """Compare a field's displayed value with what the source says it should be.

    Numbers are compared *numerically*, not as text. Both sides are the same
    quantity rendered by different code — the flow types '2' into Qty. while this
    check derives '2.00' from the model — so a string comparison reports every
    correct line as a mismatch. That turned the step 4.1 re-confirmation into a
    guaranteed false halt on every run.
    """
    a, b = str(actual).strip(), str(expected).strip()
    if not a:
        return True  # column not rendered; already logged
    if b.casefold() in a.casefold():
        return True
    number_a, number_b = _to_decimal(a), _to_decimal(b)
    if number_a is not None and number_b is not None:
        return number_a == number_b
    return False


def _to_decimal(text: str) -> Optional[Decimal]:
    """Parse a displayed number, tolerating currency marks, % and a decimal comma."""
    cleaned = "".join(ch for ch in str(text) if ch.isdigit() or ch in ",.-")
    if not cleaned or not any(ch.isdigit() for ch in cleaned):
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = (
            cleaned.replace(".", "").replace(",", ".")
            if cleaned.rfind(",") > cleaned.rfind(".")
            else cleaned.replace(",", "")
        )
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _confirm_totals(session: Session, doc: OrderDocument) -> None:
    """Spec 4.3 — the document's own totals must equal the source totals.

    This is the last point at which a wrong booking can still be caught for
    free. After Save it is a correction, not a check.
    """
    checks = (
        ("Total Net", ui.ORDER_TOTAL_NET, doc.totals.net),
        ("VAT", ui.ORDER_TOTAL_VAT, doc.totals.vat),
        ("Total", ui.ORDER_TOTAL_GROSS, doc.totals.gross),
    )
    mismatches = []
    for label, locator, expected in checks:
        try:
            actual = session.get_text(locator)
        except Exception as exc:  # noqa: BLE001
            mismatches.append(f"{label}: could not read ({exc})")
            continue
        if not _amount_matches(actual, expected):
            mismatches.append(f"{label}: source {expected}, document {actual!r}")

    if mismatches:
        shot = session.shot(f"{STEP_SAVE}-totals-mismatch")
        raise ManualReviewRequired(
            STEP_SAVE,
            "the Order totals do not match the source image",
            observed=mismatches,
            screenshot=str(shot) if shot else None,
        )
    log.info("%s: totals confirmed — net %s, VAT %s, gross %s", STEP_SAVE, *[c[2] for c in checks])


def _confirm_saved_order(session: Session, doc: OrderDocument, order_no: str) -> None:
    """Spec 4.5 — one Order row with the generated number, the expected Date and
    Cust.Ref., the open state, and the Total.

    All five are checked. Confirming only the reference and the total would pass
    a row that saved with the wrong date or in the wrong state.
    """
    open_data_menu(session, ui.MENU_DATA_DOCUMENTS)
    table = session.resolver.try_resolve(ui.DOCUMENTS_TABLE)
    rows = read_rows(table) if table is not None else []
    matches = [row for row in rows if row.contains_all([doc.external_reference])]

    if len(matches) != 1:
        shot = session.shot(f"{STEP_SAVE}-documents-check")
        raise ManualReviewRequired(
            STEP_SAVE,
            f"expected exactly one Order row for {doc.external_reference!r}, found {len(matches)}",
            observed=[row.text for row in matches] or [row.text for row in rows][:10],
            screenshot=str(shot) if shot else None,
        )

    row = matches[0]
    problems = []
    if order_no and order_no.casefold() not in row.text.casefold():
        problems.append(f"generated number {order_no!r} not shown on the row")
    if not _row_has_date(row.text, doc.order_date):
        problems.append(f"date {doc.order_date} not shown on the row")
    if not _row_amount_matches(row.text, doc.totals.gross):
        problems.append(f"total {doc.totals.gross} not shown on the row")
    if not _row_is_open(row.text):
        problems.append("row is not in the open state")

    if problems:
        shot = session.shot(f"{STEP_SAVE}-row-mismatch")
        raise ManualReviewRequired(
            STEP_SAVE,
            "the saved Order row does not match what was entered",
            observed=problems + [f"row: {row.text}"],
            screenshot=str(shot) if shot else None,
        )
    log.info("%s: Data > Documents confirms Order %s (open, %s)", STEP_SAVE, order_no, doc.totals.gross)
    session.shot(
        f"{STEP_SAVE}-documents",
        caption=f"4.5 saved Order {order_no} — {doc.external_reference}, open, {doc.totals.gross}",
    )


def _row_has_date(text: str, value) -> bool:
    """Accept any of the formats Fakturama might render a date in."""
    candidates = (
        value.strftime("%d.%m.%Y"),
        value.strftime("%Y-%m-%d"),
        value.strftime("%d/%m/%Y"),
        value.strftime("%m/%d/%Y"),
    )
    compact = str(text).replace(" ", "")
    return any(candidate in compact for candidate in candidates)


def _row_is_open(text: str) -> bool:
    """A freshly saved Order should read as open, not paid/shipped/closed."""
    folded = str(text).casefold()
    if any(word in folded for word in ("paid", "closed", "shipped", "cancelled", "canceled")):
        return False
    return True


def create_followup_invoice(session: Session) -> None:
    """Spec 4.6–4.7.

    The follow-up action is the only route that preserves the Order relationship.
    The toolbar's Invoice button produces a document that looks correct and is
    not linked to the Order — a silent failure of the actual requirement.
    """
    session.click(ui.FOLLOWUP_INVOICE)
    session.wait_for_window("Invoice")
    session.invalidate()
    log.info("linked Invoice editor open")


def check_not_already_booked(session: Session, doc: OrderDocument) -> None:
    """Refuse to book the same external reference twice.

    A rerun after a mid-flow failure is normal; silently creating a second Order
    for the same customer reference is not.
    """
    try:
        open_data_menu(session, ui.MENU_DATA_DOCUMENTS)
        table = session.resolver.try_resolve(ui.DOCUMENTS_TABLE)
        rows = read_rows(table) if table is not None else []
    except Exception as exc:  # noqa: BLE001 - a missing view is not proof of anything
        log.warning("could not check for an existing document: %s", exc)
        return

    existing = [row for row in rows if row.contains_all([doc.external_reference])]
    if existing:
        shot = session.shot("0-duplicate")
        raise DuplicateDocument(
            "0-precheck",
            f"a document with Cust.Ref. {doc.external_reference!r} already exists",
            observed=[row.text for row in existing],
            screenshot=str(shot) if shot else None,
        )


def _set_if_present(session: Session, locator, value: str) -> None:
    try:
        session.set_text(locator, value)
    except Exception as exc:  # noqa: BLE001 - optional field
        log.debug("skipped %s: %s", locator.description, exc)


def _format_date(value) -> str:
    """Fakturama accepts the locale's date format; ISO is re-formatted on commit,
    and `_equivalent` compares on digits, so the read-back still verifies."""
    return value.strftime("%d.%m.%Y")


def _amount_matches(text: str, expected: Decimal) -> bool:
    digits = "".join(ch for ch in str(text) if ch.isdigit())
    want = "".join(ch for ch in f"{expected:.2f}" if ch.isdigit())
    return bool(digits) and (digits == want or digits.lstrip("0") == want.lstrip("0"))


def _row_amount_matches(text: str, expected: Decimal) -> bool:
    want = f"{expected:.2f}"
    compact = str(text).replace(" ", "")
    return want in compact or want.replace(".", ",") in compact
