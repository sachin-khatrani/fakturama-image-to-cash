"""Typed representation of an extracted order document.

This module is deliberately free of any UI or extraction-backend concern: it is
the contract between the extraction layer and the flow layer, and it is the only
thing the flow layer is allowed to read.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

TWO_PLACES = Decimal("0.01")


def money(value: Decimal | float | int | str) -> Decimal:
    """Coerce to a 2dp Decimal. All monetary comparisons go through this."""
    return Decimal(str(value)).quantize(TWO_PLACES)


class PaidStatus(str, Enum):
    PAID = "PAID"
    UNPAID = "UNPAID"


class PaymentMethod(str, Enum):
    """Closed set of payment methods this automation knows how to create.

    An unrecognised method is an extraction failure, not something to improvise:
    creating a payment method with the wrong Fakturama payment code produces a
    document that looks right and books wrong.
    """

    BANK_TRANSFER = "Bank Transfer"
    CREDIT_CARD = "Credit Card"
    SEPA_DIRECT_DEBIT = "SEPA Direct Debit"

    @property
    def fakturama_code(self) -> str:
        """The entry to pick in Fakturama's payment-code dropdown.

        Mapping is fixed by the specification.
        """
        return {
            PaymentMethod.BANK_TRANSFER: "Credit transfer",
            PaymentMethod.CREDIT_CARD: "Credit card",
            PaymentMethod.SEPA_DIRECT_DEBIT: "SEPA direct debit",
        }[self]


class Address(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    street: str
    zip: str
    city: str
    country: str
    additional_name: Optional[str] = None

    def matches(self, other: "Address") -> bool:
        return (
            self.name.casefold() == other.name.casefold()
            and self.zip == other.zip
            and self.city.casefold() == other.city.casefold()
        )


class Debtor(BaseModel):
    model_config = ConfigDict(frozen=True)

    company: str
    first_name: str
    last_name: str
    alias: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    customer_id: Optional[str] = None
    billing_address: Address
    delivery_address: Address
    payment_method: PaymentMethod

    @property
    def delivery_equals_billing(self) -> bool:
        """Drives spec step 2.8: reuse the main address for both roles, or not."""
        return self.billing_address == self.delivery_address


class LineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    position: int
    sku: str
    description: str
    quantity: Decimal
    unit_net_price: Decimal
    discount_percent: Decimal = Decimal("0")
    vat_percent: Decimal
    source_line_net: Decimal

    @field_validator("quantity", "unit_net_price", "discount_percent", "vat_percent", "source_line_net", mode="before")
    @classmethod
    def _as_decimal(cls, v: object) -> Decimal:
        return Decimal(str(v))

    @property
    def computed_line_net(self) -> Decimal:
        """Spec 3.16: qty x unit net x (1 - discount/100)."""
        factor = Decimal("1") - (self.discount_percent / Decimal("100"))
        return money(self.quantity * self.unit_net_price * factor)

    @property
    def product_gross_price(self) -> Decimal:
        """Spec 3.9: the Product *master* price.

        Gross = unit net x (1 + VAT/100), rounded to 2dp. The transaction-line
        discount is deliberately NOT applied here -- it belongs to the order
        line, not to the product record.
        """
        return money(self.unit_net_price * (Decimal("1") + self.vat_percent / Decimal("100")))

    @property
    def vat_name(self) -> str:
        """Fakturama VAT record name, e.g. 'VAT 19%'."""
        pct = self.vat_percent.normalize()
        if pct == pct.to_integral_value():
            pct = pct.to_integral_value()
        return f"VAT {pct}%"


class Totals(BaseModel):
    model_config = ConfigDict(frozen=True)

    net: Decimal
    vat: Decimal
    gross: Decimal

    @field_validator("net", "vat", "gross", mode="before")
    @classmethod
    def _as_decimal(cls, v: object) -> Decimal:
        return Decimal(str(v))


class Payment(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: PaymentMethod
    status: PaidStatus
    payment_date: Optional[_dt.date] = None

    @property
    def is_paid(self) -> bool:
        return self.status is PaidStatus.PAID


class OrderDocument(BaseModel):
    """The validated result of reading the source image."""

    model_config = ConfigDict(frozen=True)

    external_reference: str
    order_date: _dt.date
    currency: str = "EUR"
    debtor: Debtor
    payment: Payment
    items: list[LineItem] = Field(min_length=1)
    totals: Totals

    @property
    def vat_names(self) -> list[str]:
        """Distinct VAT records this document needs, in first-seen order."""
        seen: list[str] = []
        for item in self.items:
            if item.vat_name not in seen:
                seen.append(item.vat_name)
        return seen
