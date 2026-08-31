"""Fakturama locator catalogue.

Every control the flow touches is described here, once. When Fakturama's layout
changes, this is the file that changes — the flow modules stay as they are.

IMPORTANT — read before running against a real installation:

These descriptions are written from the specification and its screenshots. They
have NOT been confirmed against a live Fakturama on this machine, because none
was installed here. The labels below are the ones the specification names, and
the grounding ladder is built to tolerate the difference between a label and its
underlying widget — but the accessible names Fakturama actually publishes must
be confirmed before trusting a run. That is what the inspector is for:

    python -m fakturama_automation.uia.inspector --window Fakturama --output tree.txt
    python -m fakturama_automation.uia.inspector --probe-grid

Correct any label here that the dump contradicts. Guessing a control name and
guessing a screen coordinate are the same mistake; the inspector is the fix for
both.
"""

from __future__ import annotations

from ..uia.locators import Locator

# --------------------------------------------------------------------------- #
# application chrome
# --------------------------------------------------------------------------- #

TOOLBAR_ORDER = Locator(
    "Order button in the top toolbar", control_type="Button", name="Order", name_contains=True
)
TOOLBAR_SAVE = Locator("toolbar Save control", control_type="Button", name="Save", name_contains=True)

MENU_DATA = Locator("Data menu", control_type="MenuItem", name="Data", name_contains=True)
MENU_DATA_VATS = Locator("Data > VATs", control_type="MenuItem", name="VAT", name_contains=True)
MENU_DATA_PAYMENTS = Locator(
    "Data > terms of payment", control_type="MenuItem", name="payment", name_contains=True
)
MENU_DATA_DOCUMENTS = Locator(
    "Data > Documents", control_type="MenuItem", name="Documents", name_contains=True
)

NEW_CONTACT = Locator(
    "New Contact in the left New panel", control_type="Button", name="New Contact", name_contains=True
)
NEW_PRODUCT = Locator("New product", control_type="Button", name="New product", name_contains=True)

# --------------------------------------------------------------------------- #
# order editor
# --------------------------------------------------------------------------- #

ORDER_NO = Locator("Order No.", control_type="Edit", label="No.")
ORDER_DATE = Locator("Order Date", control_type="Edit", label="Date")
ORDER_CUST_REF = Locator("Cust.Ref.", control_type="Edit", label="Cust.Ref.")

ORDER_PRICE_MODE_NET = Locator("document price mode Net", control_type="RadioButton", name="Net")
ORDER_VAT_MODE = Locator("VAT mode", control_type="ComboBox", label="VAT")

ORDER_DISCOUNT = Locator("order Discount", control_type="Edit", label="Discount")
ORDER_SHIPPING = Locator("order Shipping", control_type="ComboBox", label="Shipping")

ORDER_TOTAL_NET = Locator("Total Net", control_type="Text", label="Total Net", label_side="right")
ORDER_TOTAL_VAT = Locator("Total VAT", control_type="Text", label="VAT", label_side="right")
ORDER_TOTAL_GROSS = Locator("Total", control_type="Text", label="Total", label_side="right")

# The two icons beside Addresses. The specification distinguishes them only by
# position: the upper one opens the existing-contact selector, the lower green +
# starts a brand-new Debtor. Picking the wrong one silently creates a duplicate
# customer, so the sibling count is asserted — see uia/locators.py::_by_index.
ADDRESS_SELECT_ICON = Locator(
    "upper existing-contact icon beside Addresses",
    control_type="Button",
    label="Addresses",
    index=0,
    expect_siblings=2,
)
ADDRESS_NEW_ICON = Locator(
    "lower green + icon beside Addresses",
    control_type="Button",
    label="Addresses",
    index=1,
    expect_siblings=2,
)

ORDER_INVOICE_ADDRESS = Locator(
    "Invoice address field", control_type="Edit", label="Invoice address", label_side="below"
)
ORDER_DELIVERY_ADDRESS = Locator(
    "Delivery address field", control_type="Edit", label="Delivery address", label_side="below"
)

PRODUCT_SELECT_ICON = Locator(
    "upper Product-selection icon beside the Items table",
    control_type="Button",
    label="Items",
    index=0,
    expect_siblings=2,
)

FOLLOWUP_INVOICE = Locator(
    "Invoice in the 'Create a follow-up document' area",
    control_type="Button",
    name="Invoice",
    within=Locator("follow-up document area", control_type="Group", name="follow-up", name_contains=True),
)

# --------------------------------------------------------------------------- #
# selector dialogs
# --------------------------------------------------------------------------- #

ADDRESS_DIALOG_TITLE = "Select the address"
PRODUCT_DIALOG_TITLE = "Select a product"

DIALOG_SEARCH = Locator("dialog search box", control_type="Edit", label="Search", label_side="right")
DIALOG_RESULTS = Locator("dialog result list", control_type="Table")
DIALOG_RESULTS_ALT = Locator("dialog result list (List)", control_type="List")
DIALOG_OK = Locator("dialog OK", control_type="Button", name="OK")
DIALOG_CANCEL = Locator("dialog Cancel", control_type="Button", name="Cancel")

# --------------------------------------------------------------------------- #
# debtor editor
# --------------------------------------------------------------------------- #

DEBTOR_CUSTOMER_ID = Locator("Customer ID", control_type="Edit", label="Customer ID")
DEBTOR_COMPANY = Locator("Company", control_type="Edit", label="Company")
DEBTOR_FIRST_NAME = Locator("First Name", control_type="Edit", label="First Name")
DEBTOR_LAST_NAME = Locator("Name", control_type="Edit", label="Name")
DEBTOR_SALUTATION = Locator("Salutation", control_type="ComboBox", label="Salutation")

TAB_ADDRESSES = Locator("Addresses tab", control_type="TabItem", name="Addresses")
TAB_MISCELLANEOUS = Locator("Miscellaneous tab", control_type="TabItem", name="Miscellaneous")
TAB_PAYMENT = Locator("Payment tab", control_type="TabItem", name="Payment")

ADDR_STREET = Locator("address Street", control_type="Edit", label="Street")
ADDR_ZIP = Locator("address ZIP", control_type="Edit", label="ZIP")
ADDR_CITY = Locator("address City", control_type="Edit", label="City")
ADDR_COUNTRY = Locator("address Country", control_type="ComboBox", label="Country")
ADDR_EMAIL = Locator("address E-Mail", control_type="Edit", label="E-Mail")
ADDR_PHONE = Locator("address Telephone", control_type="Edit", label="Telephone")

ROLE_INVOICE_ADDRESS = Locator(
    "Invoice address role", control_type="CheckBox", name="Invoice address", name_contains=True
)
ROLE_DELIVERY_ADDRESS = Locator(
    "Delivery address role", control_type="CheckBox", name="Delivery address", name_contains=True
)

MISC_ALIAS = Locator("Alias name", control_type="Edit", label="Alias name")
MISC_DISCOUNT = Locator("Debtor Discount", control_type="Edit", label="Discount")
MISC_NET_GROSS = Locator("Net or Gross", control_type="ComboBox", label="Net or Gross")

DEBTOR_PAYMENT_METHOD = Locator("Debtor payment method", control_type="ComboBox", label="Payment")

# --------------------------------------------------------------------------- #
# payment-method (terms of payment) editor
# --------------------------------------------------------------------------- #

LIST_NEW_ICON = Locator(
    "green + at the upper-right of the list", control_type="Button", name="New", name_contains=True
)
LIST_SEARCH = Locator("list search box", control_type="Edit", label="Search", label_side="right")

PAYMENT_NAME = Locator("payment Name", control_type="Edit", label="Name")
PAYMENT_DESCRIPTION = Locator("payment Description", control_type="Edit", label="Description")
PAYMENT_ACCOUNT = Locator("payment Account", control_type="Edit", label="Account")
PAYMENT_CODE = Locator("payment code dropdown", control_type="ComboBox", label="code")
PAYMENT_CASH_DISCOUNT = Locator("Cash discount", control_type="Edit", label="Cash discount")
PAYMENT_DISCOUNT_DAYS = Locator("Discount Days", control_type="Edit", label="Discount Days")
PAYMENT_NET_DAYS = Locator("Net Days", control_type="Edit", label="Net Days")

# --------------------------------------------------------------------------- #
# VAT editor
# --------------------------------------------------------------------------- #

VAT_NAME = Locator("VAT Name", control_type="Edit", label="Name")
VAT_DESCRIPTION = Locator("VAT Description", control_type="Edit", label="Description")
VAT_CODE = Locator("VAT code (E-Invoice)", control_type="ComboBox", label="VAT code")
VAT_VALUE = Locator("VAT Value", control_type="Edit", label="Value")
VAT_STANDARD_RATE_CODE = "S (Standard rate)"

# --------------------------------------------------------------------------- #
# product editor
# --------------------------------------------------------------------------- #

PRODUCT_ITEM_NUMBER = Locator("Item Number", control_type="Edit", label="Item Number")
PRODUCT_NAME = Locator("product Name", control_type="Edit", label="Name")
PRODUCT_DESCRIPTION = Locator("product Description", control_type="Edit", label="Description")
PRODUCT_PRICE_GROSS = Locator("Price (gross)", control_type="Edit", label="Price", label_side="right")
PRODUCT_COST_PRICE = Locator("cost price (net)", control_type="Edit", label="cost price")
PRODUCT_VAT = Locator("product VAT", control_type="ComboBox", label="VAT")
PRODUCT_STOCK = Locator("Stock", control_type="Edit", label="Stock")

# --------------------------------------------------------------------------- #
# invoice editor
# --------------------------------------------------------------------------- #

INVOICE_NO = Locator("Invoice No.", control_type="Edit", label="No.")
INVOICE_CUST_REF = Locator("Invoice Cust.Ref.", control_type="Edit", label="Cust.Ref.")
INVOICE_PAYMENT_METHOD = Locator("Invoice payment method", control_type="ComboBox", label="Payment")
INVOICE_PAID = Locator("paid checkbox", control_type="CheckBox", name="paid", name_contains=True)
INVOICE_PAYMENT_DATE = Locator("payment date", control_type="Edit", label="payment date")
INVOICE_PAID_VALUE = Locator("paid Value", control_type="Edit", label="Value")

# --------------------------------------------------------------------------- #
# documents view
# --------------------------------------------------------------------------- #

DOCUMENTS_TABLE = Locator("Documents table", control_type="Table")
DOCUMENTS_SEARCH = Locator("Documents search", control_type="Edit", label="Search", label_side="right")
