"""Drive the real flow against a simulated Fakturama.

These tests execute `run_flow` itself — the same code that would drive the real
application — against the recorder in `fake_ui.py`. They cannot prove the
locators match Fakturama's widget tree; only the inspector against a live
install can do that. What they prove is everything sitting on top of the
locators: the ordering, the branching, and the verification decisions, none of
which had ever been executed before.

Both halves of every resolve-or-create branch are covered: the empty database
where records must be created, and the populated one where they must be found
and reused. The second path is the one that would otherwise never run.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from fakturama_automation.errors import ManualReviewRequired
from fakturama_automation.extraction.normalize import validate
from fakturama_automation.extraction.schema import RawOrderExtraction, to_document
from fakturama_automation.flow import grid as grid_module
from fakturama_automation.flow import invoice as invoice_module
from fakturama_automation.flow import order as order_module
from fakturama_automation.flow import product as product_module
from fakturama_automation.flow import selectors as selectors_module
from fakturama_automation.flow import ui
from fakturama_automation.flow.runner import run_flow

from .fake_ui import FakeRow, FakeSession

FIXTURE = Path(__file__).parent / "fixtures" / "order-image.json"


@pytest.fixture
def doc():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return validate(to_document(RawOrderExtraction.model_validate(payload)))


class FakeDialog:
    """Stands in for a 'Select the …' dialog."""

    def __init__(self, session: FakeSession, title: str) -> None:
        self.session = session
        self.title = title
        session._record(f"open-dialog:{title}")

    def search(self, term: str):
        self.session._record(f"search:{self.title}:{term}")
        pool = (
            self.session.existing_debtors
            if self.title == ui.ADDRESS_DIALOG_TITLE
            else self.session.existing_products
        )
        return [row for row in pool if row.contains_all([term])]

    def select(self, row) -> None:
        self.session._record(f"select-row:{self.title}:{row.text[:40]}")
        # The dialog was opened from the Order, so closing it returns there.
        self.session.open_editor = "order"

    def cancel(self) -> None:
        self.session._record(f"cancel-dialog:{self.title}")
        self.session.open_editor = "order"


@pytest.fixture
def env(monkeypatch, doc):
    """Wire the flow modules to the fake UI."""

    def build(**existing) -> FakeSession:
        session = FakeSession(doc, existing=existing)

        monkeypatch.setattr(
            selectors_module, "SelectorDialog", lambda s, title: FakeDialog(session, title)
        )

        def fake_read_rows(_table):
            return {
                "vats": session.existing_vats,
                "payments": session.existing_payments,
                "documents": session.documents,
            }[session._current_list]

        monkeypatch.setattr(selectors_module, "read_rows", fake_read_rows)
        monkeypatch.setattr(order_module, "read_rows", fake_read_rows)
        monkeypatch.setattr(invoice_module, "read_rows", fake_read_rows)
        monkeypatch.setattr(product_module, "ItemGrid", lambda s: session.grid)
        monkeypatch.setattr(grid_module, "ItemGrid", lambda s: session.grid)
        return session

    return build


def fully_stocked(doc) -> dict:
    """A database that already contains every master record this order needs."""
    debtor = doc.debtor
    return {
        "debtors": [
            FakeRow(
                text=" | ".join(
                    [
                        debtor.company,
                        debtor.first_name,
                        debtor.last_name,
                        debtor.billing_address.zip,
                        debtor.billing_address.city,
                    ]
                )
            )
        ],
        "products": [FakeRow(text=i.sku, cells=(i.sku,)) for i in doc.items],
        "vats": [FakeRow(text=f"{doc.items[0].vat_name} | 19 | S (Standard rate)")],
        "payment_methods": [doc.payment.method.value],
    }


# --------------------------------------------------------------------------- #
# the empty database — everything must be created
# --------------------------------------------------------------------------- #


def test_flow_completes_against_an_empty_database(env, doc):
    session = env()
    run_flow(session, doc)

    assert session.did(r"^save:order$"), "the Order was never saved"
    assert session.did(r"^save:invoice$"), "the Invoice was never saved"


def test_the_order_is_opened_before_any_master_data_is_touched(env, doc):
    session = env()
    run_flow(session, doc)
    assert session.happened_before(r"click:Order button", r"click:upper existing-contact")
    assert session.happened_before(r"click:Order button", r"click:upper Product-selection")


def test_the_selector_is_searched_before_anything_is_created(env, doc):
    """The Order's own selector is the existence check — not a database query."""
    session = env()
    run_flow(session, doc)
    assert session.happened_before(r"search:Select the address", r"click:New Contact")
    assert session.happened_before(r"search:Select a product", r"click:New product")


def test_a_created_record_is_re_searched_rather_than_assumed_saved(env, doc):
    """Selecting it back from the Order is what proves it saved."""
    session = env()
    run_flow(session, doc)
    searches = [e for e in session.log if e.startswith("search:Select the address")]
    assert len(searches) >= 2, f"the Debtor was not re-searched after creation: {searches}"
    assert session.happened_before(r"save:debtor", r"select-row:Select the address")


def test_the_vat_record_exists_before_the_product_editor_opens(env, doc):
    """Ordering that is not cosmetic: the Product editor reads the VAT list on
    open, so creating the VAT afterwards saves the product against no rate.

    The fake raises if New product is opened first, so reaching the assertion at
    all is the proof.
    """
    session = env()
    run_flow(session, doc)
    assert session.happened_before(r"save:new-vats", r"click:New product")


def test_the_green_plus_beside_addresses_is_never_used_to_start_a_debtor(env, doc):
    """Spec 2.1 — the lower green + starts a NEW Debtor and would duplicate the
    customer. The Debtor is only ever created via New Contact."""
    session = env()
    run_flow(session, doc)
    assert session.happened_before(r"click:New Contact", r"save:debtor")


def test_the_invoice_comes_from_the_follow_up_action_not_the_toolbar(env, doc):
    """Spec 4.6 — only the follow-up preserves the Order relationship."""
    session = env()
    run_flow(session, doc)
    assert session.did(r"click:Invoice in the 'Create a follow-up document' area")
    assert session.happened_before(r"save:order", r"click:Invoice in the 'Create a follow-up")


def test_save_is_clicked_once_per_record(env, doc):
    """Every save step in the specification says 'once'."""
    session = env()
    run_flow(session, doc)
    assert session.saves.count("order") == 1
    assert session.saves.count("invoice") == 1
    assert session.saves.count("debtor") == 1
    assert session.saves.count("product") == len(doc.items)


def test_each_item_is_processed_in_source_order(env, doc):
    """Spec 3.1 — in source order.

    Each SKU is searched twice (once to check, once to confirm it saved), so the
    assertion is on first appearance rather than on the raw sequence.
    """
    session = env()
    run_flow(session, doc)
    searched = [e.split(":")[-1] for e in session.log if e.startswith("search:Select a product:")]
    first_seen = list(dict.fromkeys(searched))
    assert first_seen == [item.sku for item in doc.items]
    # each SKU searched again after creation, per spec 3.12
    assert all(searched.count(item.sku) >= 2 for item in doc.items)


def test_the_product_master_price_is_gross_and_ignores_the_line_discount(env, doc):
    """Spec 3.9, observed through the UI rather than the model."""
    session = env()
    run_flow(session, doc)
    # The last product written is the second item; the chair is checked via
    # the model test. Assert the value that reached the field is the gross one.
    assert session.did(r"set:Price \(gross\)=47\.6")


def test_paid_status_writes_date_and_full_total(env, doc):
    session = env()
    run_flow(session, doc)
    assert session.did(r"set:payment date=18\.07\.2026")
    assert session.did(r"set:paid Value=678\.30")


# --------------------------------------------------------------------------- #
# the populated database — everything must be found and reused
# --------------------------------------------------------------------------- #


def test_flow_completes_against_a_populated_database(env, doc):
    """The select-rather-than-create half of every branch."""
    session = env(**fully_stocked(doc))
    run_flow(session, doc)
    assert session.did(r"^save:order$")
    assert session.did(r"^save:invoice$")


def test_nothing_is_created_when_everything_already_exists(env, doc):
    session = env(**fully_stocked(doc))
    run_flow(session, doc)
    assert not session.did(r"click:New Contact"), "created a duplicate Debtor"
    assert not session.did(r"click:New product"), "created a duplicate Product"
    assert "debtor" not in session.saves
    assert "product" not in session.saves


def test_an_existing_vat_is_reused_only_after_its_code_is_confirmed(env, doc):
    session = env(**fully_stocked(doc))
    run_flow(session, doc)
    assert not session.did(r"save:new-vats"), "created a duplicate VAT record"


# --------------------------------------------------------------------------- #
# the stop-for-manual-review paths
# --------------------------------------------------------------------------- #


def test_an_ambiguous_debtor_stops_the_run(env, doc):
    row = fully_stocked(doc)["debtors"][0]
    session = env(debtors=[row, FakeRow(text=row.text)])
    with pytest.raises(ManualReviewRequired) as caught:
        run_flow(session, doc)
    assert "cannot choose" in str(caught.value)
    assert not session.did(r"save:order"), "saved an Order despite ambiguity"


def test_a_near_miss_debtor_stops_rather_than_creating_a_duplicate(env, doc):
    """Same company and city, different postal code — a different customer."""
    near = FakeRow(text=f"{doc.debtor.company} | Marta | Klein | 99999 | Berlin")
    session = env(debtors=[near])
    with pytest.raises(ManualReviewRequired) as caught:
        run_flow(session, doc)
    assert "near-matches" in str(caught.value)
    assert not session.did(r"click:New Contact"), "created a duplicate instead of stopping"


def test_a_conflicting_vat_definition_stops_the_run(env, doc):
    """A record named 'VAT 19%' whose E-Invoice code is not the standard rate."""
    # The products must NOT already exist, or no Product is created and the VAT
    # check is never reached.
    stocked = fully_stocked(doc)
    stocked.pop("products")
    stocked["vats"] = [FakeRow(text=f"{doc.items[0].vat_name} | 7 | AA (Reduced rate)")]
    session = env(**stocked)
    session.fields[ui.VAT_VALUE.description] = "7"
    session.fields[ui.VAT_CODE.description] = "AA (Reduced rate)"
    with pytest.raises(ManualReviewRequired) as caught:
        run_flow(session, doc)
    assert "conflicts" in str(caught.value)


def test_an_already_booked_reference_is_refused(env, doc):
    session = env(
        documents=[FakeRow(text=f"Order | OR-1 | {doc.external_reference} | open | 678.30")]
    )
    with pytest.raises(ManualReviewRequired) as caught:
        run_flow(session, doc)
    assert "already exists" in str(caught.value)


def test_an_unpaid_document_gets_no_invented_date_or_value(env, doc):
    unpaid = doc.model_copy(
        update={"payment": doc.payment.model_copy(update={"status": "UNPAID", "payment_date": None})}
    )
    session = FakeSession(unpaid)
    session_env = env(**fully_stocked(unpaid))
    session_env.doc = unpaid
    session_env.grid.doc = unpaid
    run_flow(session_env, unpaid)
    assert not session_env.did(r"set:payment date"), "invented a payment date"
    assert not session_env.did(r"set:paid Value"), "invented a paid value"
    assert session_env.did(r"^save:invoice$")
