"""The reconciliation gate.

Extraction output is not trusted until the numbers agree with each other. A
misread digit almost always breaks one of these identities, which makes this the
cheapest meaningful validation available -- and it costs no API call.

An arithmetic failure is a hard stop. Booking a wrong price into accounting
records is a materially worse outcome than not booking one.
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal, InvalidOperation

from ..errors import ExtractionError
from ..models import OrderDocument, money

# A cent of slack, to absorb the source document rounding its own totals.
TOLERANCE = Decimal("0.01")

_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d")


def parse_date(raw: str) -> _dt.date:
    """Parse a date string, refusing anything ambiguous.

    A bare 'xx/xx/xxxx' where both components are <= 12 cannot be resolved
    without knowing the locale, so it is rejected rather than guessed.
    """
    text = raw.strip()
    ambiguous = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if ambiguous:
        a, b = int(ambiguous.group(1)), int(ambiguous.group(2))
        if a <= 12 and b <= 12 and a != b:
            raise ExtractionError(
                f"ambiguous date {text!r}: could be day-first or month-first"
            )
    for fmt in _DATE_FORMATS:
        try:
            return _dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ExtractionError(f"unrecognised date format: {raw!r}")


def parse_money(raw: str | float | int | Decimal) -> Decimal:
    """Parse a monetary string tolerant of thousands separators and currency marks."""
    if isinstance(raw, (int, float, Decimal)):
        return money(raw)
    text = str(raw).strip()
    text = re.sub(r"[^\d,.\-]", "", text)
    if "," in text and "." in text:
        # Whichever separator comes last is the decimal separator.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        # A single comma with exactly two trailing digits is a decimal comma.
        text = text.replace(",", "." if re.search(r",\d{2}$", text) else "")
    try:
        return money(text)
    except (InvalidOperation, ValueError) as exc:
        raise ExtractionError(f"unrecognised amount: {raw!r}") from exc


def _close(a: Decimal, b: Decimal, tolerance: Decimal = TOLERANCE) -> bool:
    return abs(a - b) <= tolerance


def reconcile(doc: OrderDocument) -> list[str]:
    """Check every arithmetic identity in the document.

    Returns the list of problems found; empty means the document adds up.
    """
    problems: list[str] = []

    for item in doc.items:
        computed = item.computed_line_net
        if not _close(computed, money(item.source_line_net)):
            problems.append(
                f"line {item.position} ({item.sku}): source line net "
                f"{money(item.source_line_net)} != computed {computed} "
                f"({item.quantity} x {item.unit_net_price} "
                f"- {item.discount_percent}%)"
            )
        if item.quantity <= 0:
            problems.append(f"line {item.position} ({item.sku}): non-positive quantity {item.quantity}")
        if item.unit_net_price < 0:
            problems.append(f"line {item.position} ({item.sku}): negative unit price {item.unit_net_price}")
        if not (Decimal("0") <= item.discount_percent < Decimal("100")):
            problems.append(f"line {item.position} ({item.sku}): discount out of range {item.discount_percent}")
        if not (Decimal("0") <= item.vat_percent <= Decimal("100")):
            problems.append(f"line {item.position} ({item.sku}): VAT out of range {item.vat_percent}")

    net_sum = money(sum((item.computed_line_net for item in doc.items), Decimal("0")))
    if not _close(net_sum, money(doc.totals.net)):
        problems.append(f"net total {money(doc.totals.net)} != sum of lines {net_sum}")

    vat_sum = money(
        sum(
            (item.computed_line_net * item.vat_percent / Decimal("100") for item in doc.items),
            Decimal("0"),
        )
    )
    if not _close(vat_sum, money(doc.totals.vat)):
        problems.append(f"VAT total {money(doc.totals.vat)} != sum of line VAT {vat_sum}")

    gross = money(doc.totals.net + doc.totals.vat)
    if not _close(gross, money(doc.totals.gross)):
        problems.append(f"gross total {money(doc.totals.gross)} != net + VAT {gross}")

    if doc.payment.is_paid and doc.payment.payment_date is None:
        problems.append("status is PAID but no payment date was extracted")
    if not doc.payment.is_paid and doc.payment.payment_date is not None:
        problems.append("status is not PAID but a payment date was extracted")
    if doc.payment.payment_date and doc.payment.payment_date < doc.order_date:
        problems.append(
            f"payment date {doc.payment.payment_date} precedes order date {doc.order_date}"
        )

    skus = [item.sku for item in doc.items]
    duplicates = {s for s in skus if skus.count(s) > 1}
    if duplicates:
        problems.append(f"duplicate SKUs in source items: {sorted(duplicates)}")

    return problems


def validate(doc: OrderDocument) -> OrderDocument:
    """Reconcile, or refuse to proceed."""
    problems = reconcile(doc)
    if problems:
        raise ExtractionError(
            "extracted document does not reconcile:\n  - " + "\n  - ".join(problems)
        )
    return doc
