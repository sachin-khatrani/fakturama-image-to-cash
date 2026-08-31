"""Tests for the layers that need no UI: parsing, derived values, reconciliation.

These are the parts where a bug becomes a wrong number in an accounting record
rather than a visible crash, so they are the parts worth testing hardest.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from fakturama_automation.errors import ExtractionError
from fakturama_automation.extraction.normalize import parse_date, parse_money, reconcile, validate
from fakturama_automation.extraction.schema import RawOrderExtraction, to_document
from fakturama_automation.models import PaidStatus, PaymentMethod

FIXTURE = Path(__file__).parent / "fixtures" / "order-image.json"


@pytest.fixture
def raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def build(payload: dict):
    return to_document(RawOrderExtraction.model_validate(payload))


# --------------------------------------------------------------------------- #
# the supplied sample
# --------------------------------------------------------------------------- #


def test_sample_document_reconciles(raw):
    doc = validate(build(raw))
    assert doc.external_reference == "WEB-2026-0714-A17"
    assert doc.debtor.company == "Northstar Office GmbH"
    assert doc.debtor.first_name == "Marta"
    assert doc.debtor.last_name == "Klein"
    assert doc.payment.method is PaymentMethod.BANK_TRANSFER
    assert doc.payment.status is PaidStatus.PAID
    assert doc.totals.gross == Decimal("678.30")


def test_sample_has_distinct_delivery_address(raw):
    """The sample's delivery address differs from billing, so the specification's
    'assign both roles to the main address' shortcut does not apply."""
    doc = build(raw)
    assert not doc.debtor.delivery_equals_billing


def test_line_net_excludes_vat_and_applies_discount(raw):
    doc = build(raw)
    chair, mat = doc.items
    assert chair.computed_line_net == Decimal("450.00")  # 2 x 250 x 0.9
    assert mat.computed_line_net == Decimal("120.00")  # 3 x 40


def test_product_master_price_is_gross_and_ignores_the_line_discount(raw):
    """Spec 3.9: gross = unit net x (1 + VAT/100). The 10% line discount on the
    chair belongs to the order line, not to the product record."""
    doc = build(raw)
    chair, mat = doc.items
    assert chair.product_gross_price == Decimal("297.50")  # 250 x 1.19, not 225 x 1.19
    assert mat.product_gross_price == Decimal("47.60")


def test_vat_record_name_has_no_trailing_zeros(raw):
    doc = build(raw)
    assert doc.vat_names == ["VAT 19%"]


def test_payment_code_mapping():
    assert PaymentMethod.BANK_TRANSFER.fakturama_code == "Credit transfer"
    assert PaymentMethod.CREDIT_CARD.fakturama_code == "Credit card"
    assert PaymentMethod.SEPA_DIRECT_DEBIT.fakturama_code == "SEPA direct debit"


# --------------------------------------------------------------------------- #
# the reconciliation gate — a misread digit must not reach Fakturama
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mutate, expected_fragment",
    [
        (lambda d: d["items"][0].__setitem__("unit_net_price", "260.00"), "VAT total"),
        (lambda d: d["items"][0].__setitem__("quantity", "3"), "line 1"),
        (lambda d: d["items"][0].__setitem__("discount_percent", "0"), "line 1"),
        (lambda d: d.__setitem__("gross_total", "778.30"), "gross total"),
        (lambda d: d.__setitem__("net_total", "500.00"), "net total"),
        (lambda d: d.__setitem__("vat_total", "100.00"), "gross total"),
        (lambda d: d.__setitem__("payment_date", None), "PAID but no payment date"),
        (lambda d: d.__setitem__("payment_date", "2026-07-01"), "precedes order date"),
        (lambda d: d["items"][1].__setitem__("sku", "CHR-ERG-01"), "duplicate SKUs"),
    ],
)
def test_arithmetic_faults_are_rejected(raw, mutate, expected_fragment):
    payload = copy.deepcopy(raw)
    mutate(payload)
    with pytest.raises(ExtractionError) as caught:
        validate(build(payload))
    assert expected_fragment in str(caught.value)


def test_unpaid_document_must_not_carry_a_payment_date(raw):
    payload = copy.deepcopy(raw)
    payload["paid_status"] = "UNPAID"
    problems = reconcile(build(payload))
    assert any("not PAID but a payment date" in p for p in problems)


def test_unknown_payment_method_is_rejected(raw):
    payload = copy.deepcopy(raw)
    payload["payment_method"] = "Carrier Pigeon"
    with pytest.raises(ExtractionError, match="unrecognised payment method"):
        build(payload)


def test_a_correct_document_produces_no_problems(raw):
    assert reconcile(build(raw)) == []


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def test_ambiguous_dates_are_refused_not_guessed():
    with pytest.raises(ExtractionError, match="ambiguous"):
        parse_date("07/08/2026")


def test_unambiguous_dates_parse():
    assert parse_date("2026-07-14").isoformat() == "2026-07-14"
    assert parse_date("14.07.2026").isoformat() == "2026-07-14"
    assert parse_date("25/12/2026").isoformat() == "2026-12-25"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1.234,56", "1234.56"),  # German
        ("1,234.56", "1234.56"),  # English
        ("570.00", "570.00"),
        ("570,00", "570.00"),
        ("EUR 678.30", "678.30"),
        ("1234", "1234.00"),
    ],
)
def test_money_parsing_survives_separator_conventions(text, expected):
    assert parse_money(text) == Decimal(expected)


def test_contact_name_in_a_single_field_is_split():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["contact_first_name"] = "Marta Klein"
    payload["contact_last_name"] = ""
    doc = build(payload)
    assert (doc.debtor.first_name, doc.debtor.last_name) == ("Marta", "Klein")
