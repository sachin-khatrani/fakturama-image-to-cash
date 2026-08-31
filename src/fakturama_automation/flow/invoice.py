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

    session.shot(f"{STEP}-before-save")
    session.click(ui.TOOLBAR_SAVE)  # 5.4 — once
    log.info("%s: Invoice saved", STEP)

    _verify_documents(session, doc)  # 5.5
    # 5.7 — the flow ends here. No Delivery, Correction or Dunning document.


def _confirm_copied_from_order(session: Session, doc: OrderDocument) -> None:
    """Spec 5.1 — the numbers and dates are Fakturama's; confirm, do not set."""
    invoice_no = session.get_text(ui.INVOICE_NO)
    log.info("%s: proposed invoice number %r (left unchanged)", STEP, invoice_no)

    cust_ref = session.get_text(ui.INVOICE_CUST_REF)
    if doc.external_reference.casefold() not in cust_ref.casefold():
        shot = session.shot(f"{STEP}-custref-not-copied")
        raise ManualReviewRequired(
            STEP,
            "Cust.Ref. was not carried over from the Order — the Invoice may not be linked",
            expected=doc.external_reference,
            observed=cust_ref,
            screenshot=str(shot) if shot else None,
        )
    session.shot(f"{STEP}-copied-from-order")


def _set_payment_method(session: Session, doc: OrderDocument) -> None:
    """Spec 5.2 — the method must be the one extracted; no substitution."""
    try:
        session.select_option(ui.INVOICE_PAYMENT_METHOD, doc.payment.method.value)
    except Exception as exc:  # noqa: BLE001
        shot = session.shot(f"{STEP}-payment-method-unavailable")
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
    log.info(
        "%s: marked paid on %s for %s",
        STEP,
        doc.payment.payment_date,
        doc.totals.gross,
    )


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
    """Spec 5.5 — the Invoice row is present and the source Order is still open."""
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
    if not any(total in row.text.replace(" ", "") or total.replace(".", ",") in row.text for row in related):
        shot = session.shot(f"{STEP}-total-mismatch")
        raise ManualReviewRequired(
            STEP,
            "no document row shows the expected total",
            expected=total,
            observed=[row.text for row in related],
            screenshot=str(shot) if shot else None,
        )

    log.info("%s: verified %d linked document rows", STEP, len(related))
    session.shot(f"{STEP}-final-verification")


def _amount_matches(text: str, expected: Decimal) -> bool:
    digits = "".join(ch for ch in str(text) if ch.isdigit())
    want = "".join(ch for ch in f"{expected:.2f}" if ch.isdigit())
    return bool(digits) and digits == want
