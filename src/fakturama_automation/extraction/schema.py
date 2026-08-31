"""The wire schema an extractor produces, and its conversion to the domain model.

Deliberately separate from `models.py`. The wire schema is flat, all-strings, and
transcription-shaped: it asks the extractor to report *what is printed*, not to
compute anything. Every number stays a string until `normalize` parses it, so a
thousands separator or a decimal comma is a parsing concern rather than a silent
float coercion.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ..errors import ExtractionError
from ..models import (
    Address,
    Debtor,
    LineItem,
    OrderDocument,
    PaidStatus,
    Payment,
    PaymentMethod,
    Totals,
)
from .normalize import parse_date, parse_money


class RawAddress(BaseModel):
    name: str = Field(description="Recipient name exactly as printed")
    street: str = Field(description="Street and house number")
    zip: str = Field(description="Postal code")
    city: str
    country: str


class RawLineItem(BaseModel):
    position: int = Field(description="1-based row number as printed")
    sku: str = Field(description="Article/SKU code exactly as printed")
    description: str
    quantity: str = Field(description="Quantity as printed, digits only")
    unit_net_price: str = Field(description="Unit net price as printed")
    discount_percent: str = Field(description="Line discount percent; '0' if none")
    vat_percent: str = Field(description="VAT percent for this line")
    line_net: str = Field(description="Line net total exactly as printed")


class RawOrderExtraction(BaseModel):
    """What the extractor is asked to return. Transcription only, no arithmetic."""

    external_reference: str = Field(description="External/customer reference of the order")
    order_date: str = Field(description="Order date exactly as printed")
    currency: str = Field(default="EUR", description="ISO currency code")

    company: str = Field(description="Customer company name")
    contact_first_name: str = Field(description="Contact person's given name")
    contact_last_name: str = Field(description="Contact person's family name")
    customer_alias: Optional[str] = Field(default=None, description="Customer alias/short code")
    customer_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    billing_address: RawAddress
    delivery_address: RawAddress

    payment_method: str = Field(
        description="Payment method exactly as printed, e.g. 'Bank Transfer'"
    )
    paid_status: str = Field(description="'PAID' or 'UNPAID'")
    payment_date: Optional[str] = Field(
        default=None, description="Payment date if the document shows one, else null"
    )

    items: list[RawLineItem] = Field(min_length=1)

    net_total: str
    vat_total: str
    gross_total: str


def _split_contact(first: str, last: str) -> tuple[str, str]:
    """Tolerate an extractor that puts the whole name in one field."""
    first, last = first.strip(), last.strip()
    if last:
        return first, last
    parts = first.split()
    if len(parts) >= 2:
        return " ".join(parts[:-1]), parts[-1]
    return first, ""


def _payment_method(raw: str) -> PaymentMethod:
    normalized = " ".join(raw.strip().split()).casefold()
    for method in PaymentMethod:
        if method.value.casefold() == normalized:
            return method
    aliases = {
        "banktransfer": PaymentMethod.BANK_TRANSFER,
        "wire transfer": PaymentMethod.BANK_TRANSFER,
        "creditcard": PaymentMethod.CREDIT_CARD,
        "sepa": PaymentMethod.SEPA_DIRECT_DEBIT,
        "sepa direct debit": PaymentMethod.SEPA_DIRECT_DEBIT,
        "direct debit": PaymentMethod.SEPA_DIRECT_DEBIT,
    }
    if normalized.replace(" ", "") in aliases:
        return aliases[normalized.replace(" ", "")]
    if normalized in aliases:
        return aliases[normalized]
    raise ExtractionError(
        f"unrecognised payment method {raw!r}; known: "
        f"{', '.join(m.value for m in PaymentMethod)}"
    )


def _paid_status(raw: str) -> PaidStatus:
    normalized = raw.strip().upper()
    if normalized in ("PAID", "BEZAHLT"):
        return PaidStatus.PAID
    if normalized in ("UNPAID", "OPEN", "OFFEN", "NOT PAID"):
        return PaidStatus.UNPAID
    raise ExtractionError(f"unrecognised paid status: {raw!r}")


def _address(raw: RawAddress) -> Address:
    return Address(
        name=raw.name.strip(),
        street=raw.street.strip(),
        zip=raw.zip.strip(),
        city=raw.city.strip(),
        country=raw.country.strip(),
    )


def to_document(raw: RawOrderExtraction) -> OrderDocument:
    """Convert a transcription into the validated domain model.

    Parsing failures raise `ExtractionError`; arithmetic checks happen afterwards
    in `normalize.validate`.
    """
    first, last = _split_contact(raw.contact_first_name, raw.contact_last_name)
    status = _paid_status(raw.paid_status)
    payment_date = None
    if raw.payment_date and raw.payment_date.strip().lower() not in ("", "none", "null", "-"):
        payment_date = parse_date(raw.payment_date)

    debtor = Debtor(
        company=raw.company.strip(),
        first_name=first,
        last_name=last,
        alias=(raw.customer_alias or "").strip() or None,
        email=(raw.email or "").strip() or None,
        phone=(raw.phone or "").strip() or None,
        customer_id=(raw.customer_id or "").strip() or None,
        billing_address=_address(raw.billing_address),
        delivery_address=_address(raw.delivery_address),
        payment_method=_payment_method(raw.payment_method),
    )

    items = [
        LineItem(
            position=item.position,
            sku=item.sku.strip(),
            description=item.description.strip(),
            quantity=parse_money(item.quantity),
            unit_net_price=parse_money(item.unit_net_price),
            discount_percent=parse_money(item.discount_percent.replace("%", "")),
            vat_percent=parse_money(item.vat_percent.replace("%", "")),
            source_line_net=parse_money(item.line_net),
        )
        for item in sorted(raw.items, key=lambda i: i.position)
    ]

    return OrderDocument(
        external_reference=raw.external_reference.strip(),
        order_date=parse_date(raw.order_date),
        currency=raw.currency.strip().upper() or "EUR",
        debtor=debtor,
        payment=Payment(
            method=debtor.payment_method, status=status, payment_date=payment_date
        ),
        items=items,
        totals=Totals(
            net=parse_money(raw.net_total),
            vat=parse_money(raw.vat_total),
            gross=parse_money(raw.gross_total),
        ),
    )
