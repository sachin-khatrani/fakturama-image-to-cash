# Fakturama Image-to-Cash Automation

Turns a single order image into a saved, verified Order and its linked Invoice inside Fakturama — extracting the source data, resolving or creating the Debtor and Product master records, and applying the payment status, without hardcoded coordinates or a fixed UI layout.

The design rationale is in [DESIGN.md](DESIGN.md). This file covers how to run it, what state it is in, and what I would do next.

---

## Status, stated plainly

**Fakturama was not installed on the machine this was built on**, so the end-to-end flow has never been executed against the real application. That is the single most important thing to know before reading the rest.

What that does and does not mean:

| | |
|---|---|
| **Verified by running it** | Extraction, the reconciliation gate, and all derived values (27 tests). The grounding ladder — every rung — against a live Win32 form, including the write/read-back cycle, combo selection, and the upper-vs-lower icon case. The screenshot annotator (see [`docs/annotation-mechanism-example.png`](docs/annotation-mechanism-example.png)). |
| **Written but not executed** | The flow layer (steps 1–5) and the locator catalogue in [`flow/ui.py`](src/fakturama_automation/flow/ui.py). |
| **Deliberately not guessed** | Whether the Items table is element-addressable or canvas-rendered. The grid driver probes it at runtime and picks a strategy; the inspector answers it in one command. |

The locator catalogue is written from the specification's labels and screenshots. Rather than present those as confirmed, the repo ships the tool that confirms them — see [First run](#first-run) below. Guessing a control name and guessing a screen coordinate are the same mistake, and the same tool fixes both.

---

## Setup

Requires Windows (UI Automation) and Python 3.10+.

```bash
python -m pip install -e ".[llm,ocr,dev]"
```

That puts the package on the path and installs a `fakturama-automation` command. `pip install -r requirements.txt` also works, but then run the commands below with `PYTHONPATH=src`.

Install Fakturama from https://www.fakturama.info/download/ and run it once to create a database profile.

For the vision-LLM extractor, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` (an `ant auth login` profile also works). Neither is needed for `--extractor fixture` or `--extractor ocr`.

The OCR fallback additionally needs the Tesseract binary on `PATH`, or `TESSERACT_CMD` pointing at it.

---

## Running it

Read the image and print what was extracted — no UI, no API key:

```bash
python -m fakturama_automation assets/order-image.png --extractor fixture --fixture tests/fixtures/order-image.json --dry-run
```

The full flow against a running Fakturama:

```bash
python -m fakturama_automation assets/order-image.png --extractor llm --attach
```

Launching Fakturama first, replaying a saved transcription so no API call is made:

```bash
python -m fakturama_automation assets/order-image.png --extractor fixture --fixture tests/fixtures/order-image.json --launch --exe "C:\Program Files\Fakturama2\Fakturama.exe"
```

Useful flags: `--save-extraction out.json` (capture a transcription for replay), `--allow-duplicate` (skip the already-booked check), `--allow-any-process` (accept a title match from a process that does not look like Fakturama — see below), `--artifacts DIR`, `-v`.

**Which window it attaches to.** A title-only match is not enough: any browser tab or editor whose title mentions Fakturama matches the substring, and the automation would type into it. So the process behind the window must also look like the application — its executable names Fakturama, or it is the JVM Fakturama runs on. Browsers, editors and terminals are refused outright. `--allow-any-process` overrides this if your install is unusual.

Exit codes are meaningful — `0` success, `2` stopped for manual review, `1` a defect. A supervising process can tell "a human must look at this" from "this is broken" without parsing the log.

### First run

Before the first real run, dump what Fakturama actually exposes and correct the catalogue against it:

```bash
python -m fakturama_automation.uia.inspector --window Fakturama --output tree.txt
```

And settle the item-grid question:

```bash
python -m fakturama_automation.uia.inspector --probe-grid
```

Every table-like control is classified `element-addressable` or `CANVAS — needs keyboard + OCR`. The grid driver makes the same determination automatically at runtime; this just lets you see it first.

### Tests

```bash
python -m pytest -q
```

---

## How it is put together

```
src/fakturama_automation/
  models.py            typed order document; every derived value defined once
  errors.py            failure taxonomy — "needs a human" vs "needs a fix"
  cli.py               entry point, meaningful exit codes
  extraction/
    schema.py          wire schema (transcription only) + conversion to the model
    normalize.py       the reconciliation gate
    llm.py             vision-model extraction (primary)
    ocr.py             Tesseract fallback, label-adjacency based
    fixture.py         replay a saved transcription — offline runs and CI
  uia/
    locators.py        the resolution ladder
    backend.py         attach/launch, act via patterns, read back every write
    waits.py           condition-based waits, incl. wait-until-stable
    inspector.py       live tree dump + grid classification
  flow/
    ui.py              the locator catalogue — one file to change on a UI change
    selectors.py       resolve-or-create, implemented once
    order.py           steps 1 and 4
    debtor.py          step 2, including payment-method creation
    product.py         step 3, including VAT creation
    grid.py            item lines: element mode or canvas mode
    invoice.py         step 5
    runner.py          the flow, top to bottom
```

Four decisions worth knowing about:

**Extraction is not trusted until it reconciles.** Line totals, the net/VAT/gross totals, and the payment fields must all agree before anything is opened. A misread digit breaks one of these identities, so this catches the failure that matters for free. It is a hard stop — booking a wrong price is worse than not booking one. Nine such faults are covered by tests.

**Nothing is located by coordinates.** Locators declare intent (`the ZIP field`, `the upper icon beside Addresses`) and a resolution ladder tries AutomationId, then accessible name, then label adjacency, then a label-anchored ordinal. SWT does not publish AutomationIds for most controls, so label adjacency carries the weight.

**Every write is read back.** `set_text` re-reads the field and compares. Comparison is lenient about the UI's own reformatting (`2026-07-14` → `14.07.2026`) and strict about content.

**Ambiguity stops the run.** Exact-match rules are strict — a Debtor matches only when company, first name, name, ZIP *and* city agree; a Product only on exact SKU; a VAT only when name, value and E-Invoice code all agree. Fuzzy matching is used only to *detect* near-misses and route them to a human, never to accept one.

---

## Coverage against the specification

Every numbered substep in the brief is implemented. The verification steps are the ones worth calling out, because a half-done check passes when it should fail:

| Step | What is checked |
|---|---|
| 1.4 / 1.7 | Proposed No. read and left alone; price mode set to Net **and** VAT mode confirmed as `With VAT` |
| 2.3 | Exact = Company + First Name + Name + ZIP + City, all five |
| 2.6 | Customer ID read and left unchanged; Salutation confirmed still `---` |
| 2.10.2 | Existing exact method reused; multiple or conflicting rows stop the run |
| 3.3 | Exact SKU as a whole token — `CHR-ERG-01` does not match `CHR-ERG-011` |
| 3.5 | Reuse requires name **and** value **and** the `S (Standard rate)` E-Invoice code; the record is opened to read the code, since the list view does not reliably show it |
| 3.9 | Gross = unit net × (1 + VAT/100); the line discount is deliberately excluded |
| 3.16 | Line price = qty × unit net × (1 − discount/100) |
| 4.1 | Addresses **and** every item line re-read against the source immediately before Save |
| 4.2 | Discount 0%; Shipping confirmed free / 0.00 |
| 4.3 | Total Net, VAT and Total all compared to source |
| 4.5 | Generated number, Date, Cust.Ref., **open state** and Total — all five |
| 5.1 | Cust.Ref., Invoice address, Delivery address, Order Date, VAT mode, item lines and totals |
| 5.3 | PAID sets paid + date + full total; not-PAID leaves all three untouched |
| 5.5 | Invoice state and Total, and the source Order still open with the same Cust.Ref. and Total |
| 5.6 | For a PAID document, the Invoice is reopened and the persisted method, paid flag, date and Value confirmed |
| 5.7 | Flow ends; no Delivery, Correction or Dunning document is created |

## What testing the grounding layer actually caught

Worth recording, because each was a silent-wrong-answer bug rather than a crash:

- **Label adjacency sized its search radius from the label's own height.** In a column-aligned form a *short* label sits *further* from its field, so `ZIP` and `City` were rejected while `Company` worked. Replaced with a nearest-label-wins rule, which also prevents a two-column form pairing a field with the wrong heading.
- **Combo selection fell back to typing the option name.** A dropped-down list selects on every keystroke, so `"Credit transfer"` walked the list and settled on **`SEPA direct debit`** — the wrong payment code, written silently. Options are now located as elements and selected through their own pattern.
- **A write could land on top of residual text**, producing `haBerlin` — plausible at a glance, wrong in the record. Writes now retry once from a verified-empty field.
- **Proximity search pulled in a combo's own drop-down button**, tripping the sibling-count guard on the upper/lower icon pair. Sub-parts of composite widgets are now excluded.

---

## Known gaps

- **Not run against Fakturama.** See [Status](#status-stated-plainly). The locator catalogue needs one pass with the inspector.
- **The sample's delivery address differs from its billing address.** The specification's step 2.8 covers only the identical case ("if billing and delivery are identical, also assign the Delivery address role"). The supplied image has a separate warehouse address, so a second address record is required. That branch is implemented (`debtor._add_delivery_address`) but it is an interpretation of a case the written procedure does not cover, and it is the first thing I would confirm.
- **Canvas-mode item lines need Tesseract.** If the grid turns out to be canvas-rendered and OCR is unavailable, the run stops for manual review rather than writing item lines it cannot verify.
- **Order-level discount and shipping** are held at 0 / free and confirmed, per step 4.2. Nothing *reads* order-level values from the image, because the sample supplies none — an image that did carry them would need extraction-schema fields added.
- **Step 5.6 reopens the Invoice only for a PAID document.** For an unpaid one there is nothing beyond what 5.5 already confirmed, and the spec makes the reopen conditional.
- **Screenshots are annotated automatically, but none are of Fakturama yet.** Every step writes a capture to `artifacts/screenshots/` with a caption bar and a red ring around the control that step acted on — the rectangle comes from the resolved element, so it is correct by construction. The example in `docs/` was produced against the Win32 test form used to validate the grounding ladder, **not** against Fakturama; real captures need a Fakturama run. No screen recording is produced.
- **One currency.** `EUR` is carried through and never converted.

---

## If I had 3 more hours

In this order:

1. **Install Fakturama and do the inspector pass.** Everything unverified above becomes verified or corrected, and this is the only work that cannot be done any other way. I would expect several labels in `flow/ui.py` to be wrong, and the ladder is built so that fixing them touches one file.
2. **Settle the item grid.** `--probe-grid` answers it in seconds. If it is NatTable-style canvas, the keyboard-plus-OCR path becomes the primary one and needs real tuning — column order, commit key, and how the grid signals a rejected value. This is the largest remaining unknown by a wide margin.
3. **Run the flow end to end against an empty profile and fix what breaks**, then run it a second time against the now-populated profile. The second run exercises the *other* half of every resolve-or-create branch — the paths that select rather than create — and those are the ones that have never executed at all.
4. **Turn the run into a regression test.** A disposable Fakturama profile, seeded empty, plus a golden-image suite for extraction that runs in CI without a UI.
5. **Then the deferred correctness work:** confirming the separate-delivery-address interpretation, annotating screenshots automatically by drawing the resolved control's rectangle onto each capture (the rectangle is already known at capture time, so this is cheap), and per-field confidence from the extractor so a borderline read routes to a human before anything is opened.

What I would *not* spend the time on: making the flow handle more document shapes. The single biggest risk here is not breadth, it is that the write path has never touched the real application.
