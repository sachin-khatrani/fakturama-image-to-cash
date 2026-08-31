# Fakturama Image-to-Cash — Design Document

**Scope:** one order image in → a saved, verified Order and its linked Invoice inside Fakturama, with Debtor and Product master data resolved or created along the way.

---

## 1. What actually makes this hard

The flow itself is a straight line. Two things make it non-trivial, and they drive every decision below.

**(a) No hardcoded coordinates, no fixed layout.** Fakturama is an Eclipse RCP / SWT desktop app. SWT widgets are backed by native Win32 windows, so they surface through UI Automation — but SWT does *not* populate `AutomationId` for most controls. So the usual "find by stable ID" strategy is unavailable. Grounding has to be built out of what *is* stable: control type, accessible name, label adjacency, and containment. Window size, DPI, theme, and locale must not matter.

**(b) The existence checks live inside the UI, not in a database.** The spec is explicit: the Order's own Debtor and Product selectors *are* the existence check. That inverts the usual "seed master data, then transact" shape into a re-entrant one — the Order editor stays open while nested creation flows (Debtor → payment method; Product → VAT) run beside it and return. The automation is therefore a small state machine over editor tabs, not a script.

Everything else — OCR, arithmetic, form filling — is ordinary work.

---

## 2. Architecture

Four layers, each independently testable. The top two never touch a screen; the bottom two never know what an invoice is.

```
  Order image
      │
  ┌───▼─────────────────────────────────────────┐
  │ 1. Extraction    vision LLM → typed record  │  pure data, no UI
  │    + normalization / arithmetic reconcile   │
  └───┬─────────────────────────────────────────┘
      │  OrderDocument (validated)
  ┌───▼─────────────────────────────────────────┐
  │ 2. Flow          resolve-or-create steps    │  knows Fakturama's
  │    order · debtor · payment · vat · product │  semantics
  │    · invoice · verify                       │
  └───┬─────────────────────────────────────────┘
      │  semantic intents: click("New Contact"), set_field("ZIP", …)
  ┌───▼─────────────────────────────────────────┐
  │ 3. Grounding     locator resolution ladder  │  knows *this* app's
  │    + waits + editor/tab management          │  widget tree
  └───┬─────────────────────────────────────────┘
      │
  ┌───▼─────────────────────────────────────────┐
  │ 4. Backend       UIAutomation / OCR / input │  knows Windows
  └─────────────────────────────────────────────┘
```

The seam that matters is between 2 and 3. The flow layer says *what* ("select the address selector next to Addresses"); the grounding layer decides *how* to find it today. When Fakturama's layout shifts, only layer 3 changes.

---

## 3. Image extraction

**Primary path: a vision LLM producing a typed schema**, not free text. The source is a clean synthetic document, but the strategy should not assume that — the point of using a vision model rather than template OCR is that it survives a photographed or reflowed document, which coordinate-template OCR does not.

The model is asked for a strict JSON object matching a Pydantic schema: order date, external reference, debtor (company, contact, alias, both addresses, email, phone), payment (method, paid status, payment date), and a line-item array (SKU, description, qty, unit net, discount %, VAT %, line net), plus the three document totals.

**Extraction is not trusted until it reconciles.** This is the cheapest, highest-value validation available and it costs nothing:

- `line_net == qty × unit_net × (1 − discount/100)`, per line, to 2dp
- `Σ line_net == net_total`
- `Σ (line_net × vat/100) == vat_total`
- `net_total + vat_total == gross_total`

An arithmetic failure means a misread digit. That is a **hard stop**, not a warning — silently booking a wrong price is far worse than not booking one. Dates are normalized to ISO and rejected if ambiguous; the paid status is a closed enum; the payment method maps to a closed enum with a fixed Fakturama payment-code mapping (Bank Transfer → Credit transfer, Credit Card → Credit card, SEPA Direct Debit → SEPA direct debit).

**Fallback path: OCR (Tesseract) + deterministic parsing**, behind the same interface and the same reconciliation gate. It exists so the pipeline is runnable and testable with no API key and no network — useful for CI and for demonstrating the flow offline. It is a fallback, not the design.

**Rejected:** template/zonal OCR keyed to pixel regions. It would work perfectly on the supplied image and break on the next one. Same failure mode as hardcoded UI coordinates, one layer up.

---

## 4. Control discovery and grounding

This is the core of the task. The approach is a **resolution ladder**: every control is described semantically once, and the resolver tries progressively weaker strategies until one succeeds, then caches the result for the lifetime of that window.

A locator declares intent, never geometry:

| Strategy | Basis | Used for |
|---|---|---|
| 1. Accessible name + control type | `Edit` named "ZIP" | named fields, most buttons |
| 2. **Label adjacency** | nearest preceding/left `Text` label, then the next focusable control in that container | unnamed SWT edits — the common case |
| 3. Containment scoping | resolve within an ancestor group ("Addresses" section, "Main address" tab) | disambiguating repeated labels |
| 4. Ordinal within a typed sibling set | "2nd `Button` in this toolbar", asserted against a known set size | toolbars, icon-only controls |
| 5. Keyboard graph | `Tab` order walked from a known anchor, verified by reading focus | anything the tree flattens |
| 6. OCR over the control's own bounding box | screenshot the *element*, not the screen | canvas-rendered widgets |

Rungs 1–5 are structural and DPI-independent. Rung 6 still uses no absolute coordinates — it crops the rectangle UIA reports for that element, so it moves when the window moves.

Three rules keep this honest:

- **Never click a raw point.** Resolve an element, then invoke it through its pattern (`Invoke`, `SelectionItem`, `Value`, `ExpandCollapse`); synthesize a click only into an element's own reported rectangle, after scrolling it into view.
- **Every write is read back.** Set the field, re-read it, compare. A silently rejected `Value` set is the single most common cause of a wrong booking, and it is trivially detectable.
- **Wait on conditions, never on the clock.** "Wait until the address-selector list stops changing for N ms," "wait until an editor whose title matches *New Order* exists." No `sleep()` as a synchronization primitive.

**The known risk, named up front:** Eclipse RCP applications frequently render document line-item grids with NatTable, which is a single canvas — no per-cell UIA elements. If Fakturama's Items table is one of these, rungs 1–4 return nothing for cells, and the grid is driven by rung 5 + rung 6 instead: click into the first cell, navigate with `Tab`/arrows, type, and verify by OCR-ing the cell rectangle. The design does not *assume* which case holds; a first-run probe determines it and the item-line driver picks a strategy accordingly. Shipping an inspector that dumps the live tree is part of this — grounding assumptions should be verified against the running app, not guessed from a screenshot.

**Two icons, one label.** The spec repeatedly distinguishes "the upper existing-contact icon" from "the lower green +", and "the upper Product-selection icon" from the green +. These are icon-only siblings, likely both unnamed. They are resolved by scoping to the container next to the labelled section and taking a verified ordinal (rung 4), with the sibling-count asserted so an added toolbar button fails loudly instead of silently clicking the wrong one — and the consequence is checked immediately: the correct icon opens a *selector dialog*; the wrong one opens a *new-record editor*. If the wrong window type appears, cancel and fail rather than continue.

---

## 5. Flow control

**Resolve-or-create, driven from the Order.** Each master-data step is the same shape:

```
search in the selector  →  classify the result
    exactly one exact match   → select, OK
    multiple / conflicting    → STOP for manual review
    none                      → Cancel, run creation branch, return, search again
```

"Exact" is defined per entity and is strict: a Debtor matches only when Company, First Name, Name, ZIP *and* City all match; a Product only on exact SKU; a VAT only when name, value *and* the E-Invoice code `S` all agree. Fuzzy matching is deliberately **not** used for the accept decision — it is used only to *detect* near-misses and route them to manual review, which is the safe direction.

**Re-entrancy.** The Order editor is never closed to create master data. Creation branches open beside it, save once, and control returns to the Order — where the *same selector is re-run*. That re-search is the verification: if the newly created Debtor or Product can be selected from the Order, it was saved correctly. No separate assertion needed, and no assumption that "Save clicked" means "record exists".

**Verification is a step, not an epilogue.** Each stage confirms before the next begins — addresses populate after Debtor selection, line price matches `qty × unit_net × (1 − disc/100)` after each item, document totals match the source before saving the Order, and the saved rows are confirmed in `Data > Documents`. The Invoice is created only from the Order's *Create a follow-up document* area, because the toolbar button would produce an unlinked invoice — a correct-looking result that fails the actual requirement.

**Failure model.** One exception type means "a human must look at this" (`ManualReviewRequired`), and it is raised on every ambiguity the spec calls out. It carries the step, what was expected, what was seen, and a screenshot. Everything else that fails is a bug and raises normally. The automation halts in place with the app left open and inspectable — it never guesses, never invents a payment date or value, and never half-fills a document and moves on.

**Idempotency.** A run is identified by the external reference. On restart, the flow can re-search `Data > Documents` for that reference and refuse to create a duplicate rather than booking the order twice.

**Observability.** Every step emits a structured log line and a screenshot to a per-run artifact directory, so a failed run can be reconstructed after the fact. This doubles as the annotated-screenshots deliverable.

---

## 6. Tradeoffs

| Decision | Cost | Why it is still right |
|---|---|---|
| UIA tree over vision/pixel automation | slow, brittle when the tree is thin, needs an inspector to develop against | it is the only approach that is layout- and DPI-independent, and it can *read back* what it wrote. A vision agent cannot prove a field committed. |
| Vision LLM over template OCR | cost, latency, nondeterminism | template OCR is hardcoded coordinates wearing a different hat. The reconciliation gate contains the nondeterminism. |
| Strict exact-match, stop on ambiguity | more manual-review halts | this writes to accounting records. A wrong Debtor on an invoice is a worse outcome than an unfinished run, by a wide margin. |
| Read-back after every write | roughly doubles UIA round-trips per field | it is the only thing that turns "I sent keystrokes" into "the value is in the field". Cheap insurance for the actual failure mode. |
| Resolution ladder over a fixed selector map | more machinery than a static map | a static map is one Fakturama release away from total failure; the ladder degrades one rung at a time. |
| Order-first, master data on demand | more re-entrancy, more state | it is what the spec requires, and it is right: pre-seeding master data cannot tell you whether *this* Fakturama instance already has the record. |

**Deliberately out of scope:** direct database or HSQLDB writes (bypasses the app's validation and the point of the exercise), Fakturama's import/export formats (same objection), any Delivery/Correction/Dunning document, and multi-currency handling.

---

## 7. If this needed to be production-grade

- Replace the first-run grounding probe with a **recorded control map** checked into the repo per Fakturama version, with the ladder as the fallback when the map misses — fast in the common case, resilient in the uncommon one.
- A **golden-image regression suite**: a set of order images with expected extracted JSON, run in CI without touching the UI.
- A **UI smoke test against a disposable Fakturama profile**, seeded empty, asserting the full flow end to end — the only way to catch a release that renames a field.
- **Queue-based operation**: watch an inbox for order images, with manual-review halts becoming tickets rather than stack traces.
- Confidence scores per extracted field, with a review threshold, so borderline reads are routed to a human before the automation opens anything at all.
