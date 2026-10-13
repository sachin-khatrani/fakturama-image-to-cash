"""Step 2 — select or create the Debtor, and the payment method it may need.

The Order editor stays open throughout. The Debtor is created *beside* it and
control returns to the Order, where the same selector is re-run. That re-search
is the verification: if the new Debtor can be picked from the Order, it saved.
"""

from __future__ import annotations

import logging

from ..errors import ManualReviewRequired
from ..models import Address, Debtor, PaymentMethod
from ..uia.backend import Session
from . import ui
from .selectors import Row, SelectorDialog, resolve_or_create

log = logging.getLogger(__name__)

STEP = "2-debtor"
STEP_PAYMENT = "2.10-payment-method"


def select_or_create_debtor(session: Session, debtor: Debtor) -> None:
    """Spec 2.1–2.13."""
    session.click(ui.ADDRESS_SELECT_ICON)  # 2.1 — upper icon, never the green +

    resolve_or_create(
        session,
        step=STEP,
        dialog_title=ui.ADDRESS_DIALOG_TITLE,
        search_term=debtor.company,
        is_exact=lambda row: _is_exact_debtor(row, debtor),
        create=lambda: _create_debtor(session, debtor),
        entity="Debtor",
        reopen=lambda: session.click(ui.ADDRESS_SELECT_ICON),  # 2.12
    )
    _confirm_addresses(session, debtor)


def _is_exact_debtor(row: Row, debtor: Debtor) -> bool:
    """Spec 2.3 — exact means Company, First Name, Name, ZIP and City all match.

    Every component must be present. A row that matches on company and city but
    carries a different postal code is a different customer, and treating it as
    the same one is the failure this whole branch exists to prevent.
    """
    return row.contains_all(
        [
            debtor.company,
            debtor.first_name,
            debtor.last_name,
            debtor.billing_address.zip,
            debtor.billing_address.city,
        ]
    )


def _confirm_addresses(session: Session, debtor: Debtor) -> None:
    """Spec 2.4 / 2.13 — the populated addresses must match the source image."""
    for locator, address, role in (
        (ui.ORDER_INVOICE_ADDRESS, debtor.billing_address, "Invoice address"),
        (ui.ORDER_DELIVERY_ADDRESS, debtor.delivery_address, "Delivery address"),
    ):
        try:
            actual = session.get_text(locator)
        except Exception as exc:  # noqa: BLE001
            raise ManualReviewRequired(
                STEP, f"could not read the populated {role} to confirm it", observed=str(exc)
            ) from exc
        missing = [
            value
            for value in (address.name, address.street, address.zip, address.city)
            if value and value.casefold() not in actual.casefold()
        ]
        if missing:
            shot = session.shot(f"{STEP}-address-mismatch")
            raise ManualReviewRequired(
                STEP,
                f"{role} populated from the selected Debtor does not match the source image",
                expected=missing,
                observed=actual,
                screenshot=str(shot) if shot else None,
            )
    log.info("%s: invoice and delivery addresses confirmed against the source", STEP)


# --------------------------------------------------------------------------- #
# creation branch
# --------------------------------------------------------------------------- #


def _create_debtor(session: Session, debtor: Debtor) -> None:
    """Spec 2.5–2.11. The Order tab stays open the whole time."""
    session.click(ui.NEW_CONTACT)  # 2.5
    session.wait_for_editor("Debtor")  # the tab is named "New Debtor"
    session.invalidate()

    # 2.6 — leave the proposed Customer ID alone; it is Fakturama's to allocate.
    # Read and record it rather than ignoring it: if the editor proposed nothing,
    # the record will not save and it is better to know that now.
    proposed_id = ""
    try:
        proposed_id = session.get_text(ui.DEBTOR_CUSTOMER_ID).strip()
    except Exception as exc:  # noqa: BLE001
        log.debug("%s: could not read the proposed Customer ID (%s)", STEP, exc)
    if proposed_id:
        log.info("%s: proposed Customer ID %r (left unchanged)", STEP, proposed_id)
    else:
        log.warning("%s: the New Debtor editor proposed no Customer ID", STEP)

    session.set_text(ui.DEBTOR_COMPANY, debtor.company)
    session.set_text(ui.DEBTOR_FIRST_NAME, debtor.first_name)
    session.set_text(ui.DEBTOR_LAST_NAME, debtor.last_name)
    _confirm_salutation_unset(session)

    _fill_main_address(session, debtor)
    _fill_miscellaneous(session, debtor)
    _ensure_payment_method(session, debtor.payment_method)

    session.shot(f"{STEP}-debtor-before-save")
    session.click(ui.TOOLBAR_SAVE)  # 2.11 — once
    log.info("%s: saved new Debtor %r", STEP, debtor.company)
    # 2.12 — back to the still-open Order, where the selector is re-run.
    session.select_editor("Order")


def _confirm_salutation_unset(session: Session) -> None:
    """Spec 2.6 — Salutation stays '---' when the source supplies none.

    The sample image carries no salutation, so this is a confirmation rather than
    a write. Checking it costs nothing and catches a dropdown that defaulted to a
    real salutation, which would put an unsourced honorific on the invoice.
    """
    try:
        actual = session.get_text(ui.DEBTOR_SALUTATION).strip()
    except Exception as exc:  # noqa: BLE001 - optional control
        log.debug("%s: could not read Salutation (%s)", STEP, exc)
        return
    if actual and actual not in ("---", "--", "", "-"):
        log.warning(
            "%s: Salutation is %r but the source supplies none; leaving it as found", STEP, actual
        )
    else:
        log.info("%s: Salutation left as %r", STEP, actual or "---")


def _fill_main_address(session: Session, debtor: Debtor) -> None:
    """Spec 2.7–2.8."""
    session.click(ui.TAB_ADDRESSES)
    # Addresses is a tab OF tabs: selecting it does not select the address
    # underneath it. Without this the fields below resolve to a tab that is not
    # in front, and the writes silently go nowhere -- the same failure the
    # 'additional address' tab had.
    session.select_editor("Main address")
    billing: Address = debtor.billing_address

    session.set_text(ui.ADDR_STREET, billing.street)
    session.set_text(ui.ADDR_ZIP, billing.zip)
    session.set_text(ui.ADDR_CITY, billing.city)
    session.select_option(ui.ADDR_COUNTRY, billing.country)
    if debtor.email:
        session.set_text(ui.ADDR_EMAIL, debtor.email)
    if debtor.phone:
        session.set_text(ui.ADDR_PHONE, debtor.phone)

    if debtor.delivery_equals_billing:
        # 2.8 — same address, both roles, one record. Do not create a second one.
        _set_address_type(session, [ui.ROLE_INVOICE, ui.ROLE_DELIVERY])
        log.info("%s: billing and delivery are identical; main address carries both roles", STEP)
    else:
        # The source gives a distinct delivery address, so the main address takes
        # only the invoice role and a second address record is required.
        _set_address_type(session, [ui.ROLE_INVOICE])
        _add_delivery_address(session, debtor)

    session.shot(f"{STEP}-main-address")


def _add_delivery_address(session: Session, debtor: Debtor) -> None:
    """Create the second address record when delivery differs from billing.

    The specification's step 2.8 covers only the identical-address case. The
    supplied sample has a separate warehouse delivery address, so this branch is
    reached on the real input — it is called out in the README as the place where
    the written procedure and the sample data diverge.
    """
    delivery = debtor.delivery_address
    try:
        session.click(ui.DEBTOR_ADD_ADDRESS)
        # The + creates an 'additional address #N' tab but does NOT bring it to
        # the front, so without this every field below is written into the main
        # address instead -- silently overwriting the billing address.
        session.select_editor("additional address")
        session.set_text(ui.ADDR_STREET, delivery.street)
        session.set_text(ui.ADDR_ZIP, delivery.zip)
        session.set_text(ui.ADDR_CITY, delivery.city)
        session.select_option(ui.ADDR_COUNTRY, delivery.country)
        _set_address_type(session, [ui.ROLE_DELIVERY])
        log.info("%s: added a separate delivery address", STEP)
    except Exception as exc:  # noqa: BLE001
        shot = session.shot(f"{STEP}-delivery-address-failed")
        raise ManualReviewRequired(
            STEP,
            "the source has a delivery address distinct from billing and it could "
            "not be added as a second address record",
            expected=f"{delivery.street}, {delivery.zip} {delivery.city}",
            observed=str(exc),
            screenshot=str(shot) if shot else None,
        ) from exc


def _fill_miscellaneous(session: Session, debtor: Debtor) -> None:
    """Spec 2.9 — alias, 0% discount, Net."""
    session.click(ui.TAB_MISCELLANEOUS)
    if debtor.alias:
        session.set_text(ui.MISC_ALIAS, debtor.alias)
    session.set_text(ui.MISC_DISCOUNT, "0")
    session.select_option(ui.MISC_NET_GROSS, "Net")


def _set_address_type(session: Session, roles: list[str]) -> None:
    """Spec 2.8 — assign the address its role(s).

    The editor has no role checkboxes; it has one "address type" field that
    takes the role names as text. Written and read back like any other field.
    """
    session.set_text(ui.ADDRESS_TYPE, ", ".join(roles))
    log.info("%s: address type set to %s", STEP, ", ".join(roles))


def _set_checkbox(session: Session, locator, checked: bool) -> None:
    control = session.find(locator)
    try:
        pattern = control.GetTogglePattern()
        if pattern is None:
            control.Click(simulateMove=False)
            return
        # ToggleState: 0 off, 1 on, 2 indeterminate.
        for _ in range(3):
            if (pattern.ToggleState == 1) == checked:
                return
            pattern.Toggle()
        raise ManualReviewRequired(
            STEP, f"could not set {locator.description} to {'checked' if checked else 'unchecked'}"
        )
    except ManualReviewRequired:
        raise
    except Exception:  # noqa: BLE001
        control.Click(simulateMove=False)


# --------------------------------------------------------------------------- #
# payment method (spec 2.10)
# --------------------------------------------------------------------------- #


def _ensure_payment_method(session: Session, method: PaymentMethod) -> None:
    """Spec 2.10 — select the exact method, creating it only if unavailable."""
    session.click(ui.TAB_PAYMENT)
    try:
        session.select_option(ui.DEBTOR_PAYMENT_METHOD, method.value)
        log.info("%s: payment method %r already available", STEP_PAYMENT, method.value)
        return
    except Exception:  # noqa: BLE001 - not available yet, create it
        log.info("%s: payment method %r unavailable, creating it", STEP_PAYMENT, method.value)

    _create_payment_method(session, method)

    # 2.10.6 — return to the Debtor editor, which stayed open underneath.
    session.select_editor("Debtor")
    session.click(ui.TAB_PAYMENT)
    try:
        session.select_option(ui.DEBTOR_PAYMENT_METHOD, method.value)
    except Exception as exc:  # noqa: BLE001
        shot = session.shot(f"{STEP_PAYMENT}-not-selectable")
        raise ManualReviewRequired(
            STEP_PAYMENT,
            f"payment method {method.value!r} is still not selectable after creation",
            observed=str(exc),
            screenshot=str(shot) if shot else None,
        ) from exc


def _create_payment_method(session: Session, method: PaymentMethod) -> None:
    """Spec 2.10.1–2.10.6. The Debtor editor stays open."""
    open_data_menu(session, ui.MENU_DATA_PAYMENTS)  # 2.10.1

    existing = _search_list(session, method.value)
    if len(existing) == 1:
        # 2.10.2 — one unambiguous exact row already exists; nothing to create.
        log.info("%s: found an existing exact payment method, reusing it", STEP_PAYMENT)
        return
    if len(existing) > 1:
        shot = session.shot(f"{STEP_PAYMENT}-ambiguous")
        raise ManualReviewRequired(
            STEP_PAYMENT,
            f"{len(existing)} payment methods match {method.value!r} exactly",
            observed=[row.text for row in existing],
            screenshot=str(shot) if shot else None,
        )

    session.click(ui.LIST_NEW_PAYMENT)  # 2.10.2
    session.invalidate()

    session.set_text(ui.PAYMENT_NAME, method.value)  # 2.10.3
    session.set_text(ui.PAYMENT_DESCRIPTION, method.value)
    # Account is deliberately left blank.

    session.select_option(ui.PAYMENT_CODE, method.fakturama_code)  # 2.10.4

    for locator in (ui.PAYMENT_CASH_DISCOUNT, ui.PAYMENT_DISCOUNT_DAYS, ui.PAYMENT_NET_DAYS):
        session.set_text(locator, "0")  # 2.10.5
    # The three Text 'unpaid'/'deposit'/'paid' fields stay blank, and
    # 'Set as standard' is deliberately not clicked.

    session.shot(f"{STEP_PAYMENT}-before-save")
    session.click(ui.TOOLBAR_SAVE)  # 2.10.6 — once
    log.info("%s: created payment method %r -> %r", STEP_PAYMENT, method.value, method.fakturama_code)


def open_data_menu(session: Session, item) -> None:
    """Open a Data list view from the Navigation View.

    The 'Data' section heading itself is not exposed as an element — only its
    entries are — so there is nothing to expand first. Clicking the entry is the
    whole interaction.
    """
    session.click(item)
    session.invalidate()


def _search_list(session: Session, term: str) -> list[Row]:
    """Search a Data list view and return the rows that match the term exactly."""
    from ..uia.waits import wait_until_stable

    from .selectors import read_rows

    try:
        session.set_text(ui.LIST_SEARCH, term, verify=False)
    except Exception:  # noqa: BLE001 - not every list view has a search box
        log.debug("no search box in this list view; scanning all rows")

    def rows() -> list[Row]:
        table = session.resolver.try_resolve(ui.DOCUMENTS_TABLE)
        return read_rows(table) if table is not None else []

    wait_until_stable(lambda: tuple(r.text for r in rows()), f"list results for {term!r}")
    return [row for row in rows() if row.contains_all([term])]
