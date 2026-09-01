"""The Order-first flow, end to end.

Reads top to bottom as the specification does. Each stage verifies before the
next begins, and the Order editor stays open across the master-data branches.
"""

from __future__ import annotations

import logging

from ..models import OrderDocument
from ..uia.backend import Session
from .debtor import select_or_create_debtor
from .invoice import complete_and_verify_invoice
from .order import (
    check_not_already_booked,
    complete_and_save_order,
    create_followup_invoice,
    open_new_order,
)
from .product import process_items

log = logging.getLogger(__name__)


def run_flow(session: Session, doc: OrderDocument, *, skip_duplicate_check: bool = False) -> None:
    """Drive one order image through to a saved, verified Invoice."""
    log.info(
        "starting flow for %s — %s, %d item(s), gross %s",
        doc.external_reference,
        doc.debtor.company,
        len(doc.items),
        doc.totals.gross,
    )

    if not skip_duplicate_check:
        check_not_already_booked(session, doc)

    # 1 — open the Order first; it stays open for everything that follows.
    order_no = open_new_order(session, doc)

    # 2 — the Order's address selector is the Debtor existence check.
    select_or_create_debtor(session, doc.debtor)

    # 3 — the Order's product selector is the Product existence check.
    grid = process_items(session, doc.items)

    # 4 — confirm against the source, save once, verify the saved row.
    complete_and_save_order(session, doc, order_no, grid)

    # 4.6 — the follow-up action, which is what keeps the Order relationship.
    create_followup_invoice(session)

    # 5 — payment status, save once, verify.
    complete_and_verify_invoice(session, doc)

    log.info("flow complete for %s", doc.external_reference)
