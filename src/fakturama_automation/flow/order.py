"""Steps 1 and 4 — open the Order, set its header, then complete and save it."""

from __future__ import annotations

import logging
from decimal import Decimal

from ..errors import DuplicateDocument, ManualReviewRequired
from ..models import OrderDocument
from ..uia.backend import Session
from . import ui
from .debtor import open_data_menu
from .selectors import read_rows

log = logging.getLogger(__name__)

STEP_OPEN = "1-open-order"
STEP_SAVE = "4-save-order"


def open_new_order(session: Session, doc: OrderDocument) -> None:
    """Spec 1.3–1.8."""
    session.click(ui.TOOLBAR_ORDER)  # 1.3
    session.wait_for_window("Order")
    session.invalidate()
    log.info("%s: New Order editor open", STEP_OPEN)

    # 1.4 — the proposed No. is Fakturama's to allocate; leave it alone.
    proposed = session.get_text(ui.ORDER_NO)
    log.info("%s: proposed order number %r (left unchanged)", STEP_OPEN, proposed)

    session.set_text(ui.ORDER_DATE, _format_date(doc.order_date))  # 1.5
    session.set_text(ui.ORDER_CUST_REF, doc.external_reference)  # 1.6

    # 1.7 — Net price mode, VAT stays 'With VAT'.
    try:
        session.click(ui.ORDER_PRICE_MODE_NET)
    except Exception as exc:  # noqa: BLE001
        raise ManualReviewRequired(
            STEP_OPEN, "could not set the document price mode to Net", observed=str(exc)
        ) from exc

    session.shot(f"{STEP_OPEN}-order-header")


def complete_and_save_order(session: Session, doc: OrderDocument) -> None:
    """Spec 4.1–4.5."""
    # 4.2 — no order-level discount or shipping unless the image supplies them.
    _set_if_present(session, ui.ORDER_DISCOUNT, "0")

    _confirm_totals(session, doc)  # 4.3

    session.shot(f"{STEP_SAVE}-before-save")
    session.click(ui.TOOLBAR_SAVE)  # 4.4 — once
    log.info("%s: Order saved", STEP_SAVE)

    _confirm_saved_order(session, doc)  # 4.5


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


def _confirm_saved_order(session: Session, doc: OrderDocument) -> None:
    """Spec 4.5 — one Order row with the expected reference, state and total."""
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
    if not _row_amount_matches(row.text, doc.totals.gross):
        shot = session.shot(f"{STEP_SAVE}-total-mismatch")
        raise ManualReviewRequired(
            STEP_SAVE,
            "the saved Order row does not show the expected total",
            expected=str(doc.totals.gross),
            observed=row.text,
            screenshot=str(shot) if shot else None,
        )
    log.info("%s: Data > Documents shows the saved Order", STEP_SAVE)
    session.shot(f"{STEP_SAVE}-documents")


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
