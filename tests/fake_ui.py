"""A fake Fakturama that records what the flow does to it.

The flow layer is the part that cannot be tested against the real application
here, and "it imports cleanly" is not evidence that it works. This stands in for
a `Session`, records every interaction, and answers reads the way Fakturama
would — so the *decisions* the flow makes become observable and assertable:

  * that the Order is opened before any master data is touched, and stays open
  * that the selector is searched before anything is created
  * that a created record is re-searched from the Order rather than assumed saved
  * that the VAT record exists before the Product editor is opened
  * that the Invoice comes from the follow-up action, never the toolbar
  * that Save is clicked exactly once per record

It is a stand-in for the widget tree, not for Fakturama's behaviour, so it
cannot prove the locators are right — only the inspector against a real install
can do that. What it does prove is that the logic sitting on top of those
locators is correct, which is otherwise entirely unverified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from fakturama_automation.errors import ControlNotFound
from fakturama_automation.flow import ui
from fakturama_automation.models import OrderDocument


@dataclass
class FakeControl:
    """Stands in for a resolved UIA element."""

    name: str = ""

    def DoubleClick(self, **_kw: Any) -> None:  # noqa: N802 - mirrors uiautomation
        pass

    def Click(self, **_kw: Any) -> None:  # noqa: N802
        pass

    def GetSelectionItemPattern(self):  # noqa: N802
        return None

    def GetTogglePattern(self):  # noqa: N802
        return None


@dataclass
class FakeRow:
    """A row in a selector dialog or list view."""

    text: str
    cells: tuple[str, ...] = ()
    control: FakeControl = field(default_factory=FakeControl)

    def contains_all(self, values: list[str]) -> bool:
        haystack = " | ".join(self.cells) if self.cells else self.text
        folded = haystack.casefold()
        return all(v.strip().casefold() in folded for v in values if v and v.strip())


@dataclass
class FakeGrid:
    """Stands in for the Items table."""

    rows: dict[int, dict[str, str]] = field(default_factory=dict)
    doc: Optional[OrderDocument] = None

    def set_cell(self, row: int, column: str, value: str) -> None:
        self.rows.setdefault(row, {})[column] = value

    def get_cell(self, row: int, column: str) -> str:
        stored = self.rows.get(row, {})
        if column in stored:
            return stored[column]
        # Fakturama computes these itself; mirror what it would show.
        if self.doc and row < len(self.doc.items):
            item = self.doc.items[row]
            if column == "Price":
                return f"{item.computed_line_net:.2f}"
            if column == "Item Number":
                return item.sku
        return ""


class FakeSession:
    """Records every interaction the flow performs."""

    def __init__(self, doc: OrderDocument, *, existing: Optional[dict] = None) -> None:
        self.doc = doc
        self.log: list[str] = []
        self.saves: list[str] = []
        self.fields: dict[str, str] = {}
        self.open_editor = "none"
        self.grid = FakeGrid(doc=doc)
        self.shots: list[str] = []

        # Which master records already exist in this fake database.
        existing = existing or {}
        self.existing_debtors: list[FakeRow] = existing.get("debtors", [])
        self.existing_products: list[FakeRow] = existing.get("products", [])
        self.existing_vats: list[FakeRow] = existing.get("vats", [])
        self.existing_payments: list[FakeRow] = existing.get("payments", [])
        self.available_payment_methods: list[str] = existing.get("payment_methods", [])

        self.documents: list[FakeRow] = existing.get("documents", [])
        self._order_no = "OR-2026-0001"
        self._last_search = ""
        self._current_list = "documents"

    # ------------------------------------------------------------------ record

    def _record(self, entry: str) -> None:
        self.log.append(entry)

    def did(self, pattern: str) -> bool:
        return any(re.search(pattern, entry) for entry in self.log)

    def index_of(self, pattern: str) -> int:
        for i, entry in enumerate(self.log):
            if re.search(pattern, entry):
                return i
        raise AssertionError(f"never happened: {pattern!r}\nlog:\n  " + "\n  ".join(self.log))

    def happened_before(self, first: str, second: str) -> bool:
        return self.index_of(first) < self.index_of(second)

    # -------------------------------------------------------------- session API

    def shot(self, label: str, highlight: Any = None, caption: Optional[str] = None) -> str:
        self.shots.append(label)
        return f"fake/{label}.png"

    def invalidate(self) -> None:
        pass

    def find(self, loc, timeout: float = 0) -> FakeControl:
        return FakeControl(loc.description)

    def exists(self, loc, timeout: float = 0) -> bool:
        return True

    @property
    def resolver(self):
        return self

    def try_resolve(self, loc):
        return FakeControl(loc.description)

    def click(self, loc, timeout: float = 0) -> FakeControl:
        self._record(f"click:{loc.description}")
        if loc is ui.MENU_DATA_VATS:
            self._current_list = "vats"
        elif loc is ui.MENU_DATA_PAYMENTS:
            self._current_list = "payments"
        elif loc is ui.MENU_DATA_DOCUMENTS:
            self._current_list = "documents"

        # Clicking a tab means we are back in the Debtor editor -- this is how the
        # flow returns from the nested payment-method editor (spec 2.10.6).
        if loc in (ui.TAB_ADDRESSES, ui.TAB_MISCELLANEOUS, ui.TAB_PAYMENT):
            self.open_editor = "debtor"

        if loc is ui.TOOLBAR_ORDER:
            self.open_editor = "order"
        elif loc is ui.NEW_CONTACT:
            self.open_editor = "debtor"
        elif loc is ui.NEW_PRODUCT:
            if not self._vat_exists_for_current_item():
                raise AssertionError(
                    "New product was opened before the required VAT record existed — "
                    "the VAT dropdown would not contain it"
                )
            self.open_editor = "product"
        elif loc is ui.LIST_NEW_ICON:
            self.open_editor = f"new-{self._current_list}"
        elif loc is ui.TOOLBAR_SAVE:
            self.saves.append(self.open_editor)
            self._commit(self.open_editor)
        elif loc is ui.FOLLOWUP_INVOICE:
            self.open_editor = "invoice"
        elif loc is ui.ADDRESS_NEW_ICON:
            self._record("click:GREEN-PLUS-address")
        return FakeControl(loc.description)

    def set_text(self, loc, value: str, verify: bool = True, attempts: int = 2) -> None:
        self.fields[loc.description] = str(value)
        self._record(f"set:{loc.description}={value}")

    def get_text(self, loc) -> str:
        return self._read(loc)

    def select_option(self, loc, option: str) -> None:
        if loc in (ui.DEBTOR_PAYMENT_METHOD, ui.INVOICE_PAYMENT_METHOD):
            if option not in self.available_payment_methods:
                raise ControlNotFound(f"option {option!r} in {loc.description}")
        self.fields[loc.description] = option
        self._record(f"select:{loc.description}={option}")

    def wait_for_window(self, title: str, timeout: float = 0):
        self._record(f"wait-window:{title}")
        return FakeControl(title)

    def wait_for_window_closed(self, title: str, timeout: float = 0) -> None:
        pass

    # ------------------------------------------------------------------- reads

    def _read(self, loc) -> str:
        doc = self.doc
        mapping = {
            ui.ORDER_NO: self._order_no,
            ui.DEBTOR_CUSTOMER_ID: "CUST-9001",
            ui.DEBTOR_SALUTATION: "---",
            ui.ORDER_VAT_MODE: "With VAT",
            ui.ORDER_SHIPPING: "Free of shipping costs",
            ui.ORDER_TOTAL_NET: f"{doc.totals.net:.2f}",
            ui.ORDER_TOTAL_VAT: f"{doc.totals.vat:.2f}",
            ui.ORDER_TOTAL_GROSS: f"{doc.totals.gross:.2f}",
            ui.INVOICE_NO: "RE-2026-0001",
            ui.INVOICE_DATE: "14.07.2026",
            ui.INVOICE_CUST_REF: doc.external_reference,
            ui.INVOICE_ORDER_DATE: doc.order_date.strftime("%d.%m.%Y"),
            ui.INVOICE_PAYMENT_METHOD: self.fields.get(
                ui.INVOICE_PAYMENT_METHOD.description, doc.payment.method.value
            ),
            ui.INVOICE_PAYMENT_DATE: self.fields.get(ui.INVOICE_PAYMENT_DATE.description, ""),
            ui.INVOICE_PAID_VALUE: self.fields.get(ui.INVOICE_PAID_VALUE.description, ""),
        }
        if loc in mapping:
            return mapping[loc]

        if loc is ui.ORDER_INVOICE_ADDRESS:
            return self._address_text(doc.debtor.billing_address)
        if loc is ui.ORDER_DELIVERY_ADDRESS:
            return self._address_text(doc.debtor.delivery_address)
        if loc is ui.VAT_NAME:
            return self.fields.get(loc.description, doc.items[0].vat_name)
        if loc is ui.VAT_VALUE:
            return self.fields.get(loc.description, str(int(doc.items[0].vat_percent)))
        if loc is ui.VAT_CODE:
            return self.fields.get(loc.description, ui.VAT_STANDARD_RATE_CODE)
        return self.fields.get(loc.description, "")

    @staticmethod
    def _address_text(address) -> str:
        return f"{address.name}\n{address.street}\n{address.zip} {address.city}\n{address.country}"

    def _vat_exists_for_current_item(self) -> bool:
        return bool(self.existing_vats)

    # -------------------------------------------------------------- committing

    def _commit(self, editor: str) -> None:
        """Persist whatever editor was open, as Fakturama would."""
        doc = self.doc
        if editor == "debtor":
            self.existing_debtors.append(
                FakeRow(
                    text=" | ".join(
                        [
                            doc.debtor.company,
                            doc.debtor.first_name,
                            doc.debtor.last_name,
                            doc.debtor.billing_address.zip,
                            doc.debtor.billing_address.city,
                        ]
                    )
                )
            )
        elif editor == "product":
            sku = self.fields.get(ui.PRODUCT_ITEM_NUMBER.description, "")
            self.existing_products.append(FakeRow(text=sku, cells=(sku,)))
        elif editor.startswith("new-vat"):
            name = self.fields.get(ui.VAT_NAME.description, "")
            self.existing_vats.append(FakeRow(text=f"{name} | 19 | S (Standard rate)"))
        elif editor.startswith("new-payment"):
            name = self.fields.get(ui.PAYMENT_NAME.description, "")
            self.existing_payments.append(FakeRow(text=name))
            self.available_payment_methods.append(name)
        elif editor == "order":
            self.documents.append(
                FakeRow(
                    text=f"Order | {self._order_no} | {doc.order_date.strftime('%d.%m.%Y')} | "
                    f"{doc.external_reference} | open | {doc.totals.gross:.2f}"
                )
            )
        elif editor == "invoice":
            state = "paid" if doc.payment.is_paid else "unpaid"
            self.documents.append(
                FakeRow(
                    text=f"Invoice | RE-2026-0001 | {doc.external_reference} | {state} | "
                    f"{doc.totals.gross:.2f}"
                )
            )
        self._record(f"save:{editor}")
