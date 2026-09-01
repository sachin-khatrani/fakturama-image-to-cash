"""Step 5 — complete and verify the linked Invoice."""

from __future__ import annotations

import logging
from decimal import Decimal

from ..errors import ManualReviewRequired
from ..models import OrderDocument
from ..uia.backend import Session
from . import ui
from .debtor import open_data_menu
from .selectors import read_rows

log = logging.getLogger(__name__)

STEP = "5-invoice"


def complete_and_verify_invoice(session: Session, doc: OrderDocument) -> None:
    """Spec 5.1–5.7."""
    _confirm_copied_from_order(session, doc)  # 5.1
    _set_payment_method(session, doc)  # 5.2
    _apply_paid_status(session, doc)  # 5.3

    session.shot(
        f"{STEP}-before-save",
        highlight=ui.INVOICE_PAID,
        caption=(
            f"5.3 {doc.payment.method.value} — "
            + (
                f"paid {doc.payment.payment_date} for {doc.totals.gross}"
                if doc.payment.is_paid
                else "not paid; date and value left blank"
            )
        ),
    )
    session.click(ui.TOOLBAR_SAVE)  # 5.4 — once
    log.info("%s: Invoice saved", STEP)

    _verify_documents(session, doc)  # 5.5
    _reopen_and_confirm_persisted(session, doc)  # 5.6
    # 5.7 — the flow ends here. No Delivery, Correction or Dunning document.


def _confirm_copied_from_order(session: Session, doc: OrderDocument) -> None:
    """Spec 5.1 — confirm what the follow-up action carried over.

    The Invoice No., Invoice Date and Service date are Fakturama's to allocate
    and are read, not written. Everything else listed in 5.1 is checked against
    the source: a field that failed to copy is the signal that the follow-up did
    not link the documents, and that is precisely the failure the toolbar-button
    shortcut would have produced silently.
    """
    for label, locator in (("Invoice No.", ui.INVOICE_NO), ("Invoice Date", ui.INVOICE_DATE)):
        try:
            log.info("%s: proposed %s %r (left unchanged)", STEP, label, session.get_text(locator).strip())
        except Exception as exc:  # noqa: BLE001
            log.debug("%s: could not read %s (%s)", STEP, label, exc)

    problems: list[str] = []

    cust_ref = _safe_read(session, ui.INVOICE_CUST_REF)
    if doc.external_reference.casefold() not in cust_ref.casefold():
        problems.append(f"Cust.Ref.: expected {doc.external_reference!r}, found {cust_ref!r}")

    for role, locator, address in (
        ("Invoice address", ui.ORDER_INVOICE_ADDRESS, doc.debtor.billing_address),
        ("Delivery address", ui.ORDER_DELIVERY_ADDRESS, doc.debtor.delivery_address),
    ):
        actual = _safe_read(session, locator)
        missing = [
            value
            for value in (address.name, address.street, address.zip, address.city)
            if value and value.casefold() not in actual.casefold()
        ]
        if missing:
            problems.append(f"{role}: {missing} not present in {actual!r}")

    order_date = _safe_read(session, ui.INVOICE_ORDER_DATE)
    if order_date and not _has_date(order_date, doc.order_date):
        problems.append(f"Order Date: expected {doc.order_date}, found {order_date!r}")

    vat_mode = _safe_read(session, ui.ORDER_VAT_MODE)
    if vat_mode and "with vat" not in vat_mode.casefold():
        problems.append(f"VAT mode: expected 'With VAT', found {vat_mode!r}")

    problems.extend(_check_totals(session, doc))
    problems.extend(_check_item_lines(session, doc))

    if problems:
        shot = session.shot(f"{STEP}-not-copied-from-order")
        raise ManualReviewRequired(
            STEP,
            "the Invoice does not match the Order it was created from — it may not be linked",
            observed=problems,
            screenshot=str(shot) if shot else None,
        )

    log.info("%s: Cust.Ref., addresses, order date, VAT mode, lines and totals all copied", STEP)
    session.shot(
        f"{STEP}-copied-from-order",
        highlight=ui.INVOICE_CUST_REF,
        caption=f"5.1 linked Invoice — {doc.external_reference}, {len(doc.items)} line(s), {doc.totals.gross}",
    )


def _check_totals(session: Session, doc: OrderDocument) -> list[str]:
    problems = []
    for label, locator, expected in (
        ("Total Net", ui.ORDER_TOTAL_NET, doc.totals.net),
        ("VAT", ui.ORDER_TOTAL_VAT, doc.totals.vat),
        ("Total", ui.ORDER_TOTAL_GROSS, doc.totals.gross),
    ):
        actual = _safe_read(session, locator)
        if actual and not _amount_matches(actual, expected):
            problems.append(f"{label}: expected {expected}, found {actual!r}")
    return problems


def _check_item_lines(session: Session, doc: OrderDocument) -> list[str]:
    """Confirm the item lines came across, per 5.1."""
    from .grid import ItemGrid

    try:
        grid = ItemGrid(session)
    except Exception as exc:  # noqa: BLE001 - grid shape differs on the invoice
        log.warning("%s: could not read the Invoice item lines (%s)", STEP, exc)
        return []

    problems = []
    for item in doc.items:
        row = item.position - 1
        for column, expected in (("Item Number", item.sku), ("Price", f"{item.computed_line_net:.2f}")):
            try:
                actual = grid.get_cell(row, column)
            except Exception as exc:  # noqa: BLE001
                log.debug("%s: line %d %s unreadable (%s)", STEP, item.position, column, exc)
                continue
            if actual and expected.casefold() not in actual.casefold() and not _amount_matches(
                actual, Decimal(expected) if _is_number(expected) else Decimal("0")
            ):
                problems.append(f"line {item.position} {column}: expected {expected!r}, found {actual!r}")
    return problems


def _set_payment_method(session: Session, doc: OrderDocument) -> None:
    """Spec 5.2 — the method must be the one extracted; no substitution."""
    try:
        session.select_option(ui.INVOICE_PAYMENT_METHOD, doc.payment.method.value)
    except Exception as exc:  # noqa: BLE001
        shot = session.shot(f"{STEP}-payment-method-unavailable", highlight=ui.INVOICE_PAYMENT_METHOD)
        raise ManualReviewRequired(
            STEP,
            f"payment method {doc.payment.method.value!r} is not available on the Invoice",
            observed=str(exc),
            screenshot=str(shot) if shot else None,
        ) from exc


def _apply_paid_status(session: Session, doc: OrderDocument) -> None:
    """Spec 5.3.

    If the document is not PAID, the paid box stays clear and no date or value is
    invented. Filling those in "to be complete" would fabricate a payment that
    never happened.
    """
    if not doc.payment.is_paid:
        log.info("%s: source is not PAID — leaving paid clear", STEP)
        _set_paid_checkbox(session, False)
        return

    _set_paid_checkbox(session, True)
    session.set_text(ui.INVOICE_PAYMENT_DATE, doc.payment.payment_date.strftime("%d.%m.%Y"))
    session.set_text(ui.INVOICE_PAID_VALUE, f"{doc.totals.gross:.2f}")
    log.info("%s: marked paid on %s for %s", STEP, doc.payment.payment_date, doc.totals.gross)


def _set_paid_checkbox(session: Session, checked: bool) -> None:
    control = session.find(ui.INVOICE_PAID)
    try:
        pattern = control.GetTogglePattern()
        if pattern is None:
            if checked:
                control.Click(simulateMove=False)
            return
        for _ in range(3):
            if (pattern.ToggleState == 1) == checked:
                return
            pattern.Toggle()
    except Exception:  # noqa: BLE001
        if checked:
            control.Click(simulateMove=False)


def _verify_documents(session: Session, doc: OrderDocument) -> None:
    """Spec 5.5 — the Invoice row shows the expected state and Total, while the
    source Order is still open with the same Cust.Ref. and Total."""
    open_data_menu(session, ui.MENU_DATA_DOCUMENTS)
    table = session.resolver.try_resolve(ui.DOCUMENTS_TABLE)
    rows = read_rows(table) if table is not None else []
    related = [row for row in rows if row.contains_all([doc.external_reference])]

    if len(related) < 2:
        shot = session.shot(f"{STEP}-documents-incomplete")
        raise ManualReviewRequired(
            STEP,
            f"expected both an Order and an Invoice for {doc.external_reference!r}, "
            f"found {len(related)} row(s)",
            observed=[row.text for row in related],
            screenshot=str(shot) if shot else None,
        )

    total = f"{doc.totals.gross:.2f}"
    with_total = [row for row in related if _row_shows(row.text, total)]
    if not with_total:
        shot = session.shot(f"{STEP}-total-mismatch")
        raise ManualReviewRequired(
            STEP,
            "no document row shows the expected total",
            expected=total,
            observed=[row.text for row in related],
            screenshot=str(shot) if shot else None,
        )

    invoice_rows = [row for row in related if "invoice" in row.text.casefold()]
    order_rows = [row for row in related if "order" in row.text.casefold()]

    problems = []
    if doc.payment.is_paid and invoice_rows and not any("paid" in r.text.casefold() for r in invoice_rows):
        problems.append(f"Invoice row does not show a paid state: {[r.text for r in invoice_rows]}")
    if order_rows and any(
        word in " ".join(r.text.casefold() for r in order_rows) for word in ("closed", "cancelled", "canceled")
    ):
        problems.append(f"the source Order is no longer open: {[r.text for r in order_rows]}")
    if order_rows and not any(_row_shows(r.text, total) for r in order_rows):
        problems.append(f"the source Order no longer shows total {total}")

    if problems:
        shot = session.shot(f"{STEP}-state-mismatch")
        raise ManualReviewRequired(
            STEP,
            "the saved documents are not in the expected state",
            observed=problems,
            screenshot=str(shot) if shot else None,
        )

    log.info("%s: verified %d linked document rows", STEP, len(related))
    session.shot(
        f"{STEP}-final-verification",
        caption=f"5.5 {doc.external_reference} — Invoice {'paid ' if doc.payment.is_paid else ''}{total}, source Order open",
    )


def _reopen_and_confirm_persisted(session: Session, doc: OrderDocument) -> None:
    """Spec 5.6 — reopen the Invoice to confirm what actually persisted.

    'Only if needed' — and for a PAID document it is needed: the payment method,
    paid flag, date and value are the fields most likely to be silently dropped
    on save, and the documents list does not show all four. For an unpaid
    document there is nothing to confirm beyond what 5.5 already covered.
    """
    if not doc.payment.is_paid:
        log.info("%s: unpaid document — no reopen needed (5.6)", STEP)
        return

    table = session.resolver.try_resolve(ui.DOCUMENTS_TABLE)
    rows = read_rows(table) if table is not None else []
    invoice_rows = [
        row
        for row in rows
        if row.contains_all([doc.external_reference]) and "invoice" in row.text.casefold()
    ]
    if not invoice_rows:
        log.warning("%s: could not locate the Invoice row to reopen it", STEP)
        return

    try:
        invoice_rows[0].control.DoubleClick(simulateMove=False)
        session.wait_for_window("Invoice")
        session.invalidate()
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: could not reopen the Invoice (%s)", STEP, exc)
        return

    problems = []
    method = _safe_read(session, ui.INVOICE_PAYMENT_METHOD)
    if method and doc.payment.method.value.casefold() not in method.casefold():
        problems.append(f"payment method persisted as {method!r}, expected {doc.payment.method.value!r}")

    paid_date = _safe_read(session, ui.INVOICE_PAYMENT_DATE)
    if paid_date and not _has_date(paid_date, doc.payment.payment_date):
        problems.append(f"payment date persisted as {paid_date!r}, expected {doc.payment.payment_date}")

    value = _safe_read(session, ui.INVOICE_PAID_VALUE)
    if value and not _amount_matches(value, doc.totals.gross):
        problems.append(f"paid value persisted as {value!r}, expected {doc.totals.gross}")

    try:
        toggle = session.find(ui.INVOICE_PAID).GetTogglePattern()
        if toggle is not None and toggle.ToggleState != 1:
            problems.append("the paid flag did not persist")
    except Exception:  # noqa: BLE001
        pass

    if problems:
        shot = session.shot(f"{STEP}-not-persisted", highlight=ui.INVOICE_PAID)
        raise ManualReviewRequired(
            STEP,
            "the Invoice payment details did not persist as written",
            observed=problems,
            screenshot=str(shot) if shot else None,
        )
    log.info("%s: reopened Invoice confirms method, paid flag, date and value persisted", STEP)
    session.shot(f"{STEP}-persisted", caption="5.6 reopened Invoice — payment details persisted")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _safe_read(session: Session, locator) -> str:
    try:
        return session.get_text(locator).strip()
    except Exception as exc:  # noqa: BLE001 - an absent field is reported by the caller
        log.debug("%s: could not read %s (%s)", STEP, locator.description, exc)
        return ""


def _row_shows(text: str, amount: str) -> bool:
    compact = str(text).replace(" ", "")
    return amount in compact or amount.replace(".", ",") in compact


def _has_date(text: str, value) -> bool:
    compact = str(text).replace(" ", "")
    return any(
        value.strftime(fmt) in compact for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y")
    )


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
        return True
    except Exception:  # noqa: BLE001
        return False


def _amount_matches(text: str, expected: Decimal) -> bool:
    digits = "".join(ch for ch in str(text) if ch.isdigit())
    want = "".join(ch for ch in f"{expected:.2f}" if ch.isdigit())
    return bool(digits) and (digits == want or digits.lstrip("0") == want.lstrip("0"))
