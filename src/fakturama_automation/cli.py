"""Command-line entry point.

    # read the image and print what was extracted — no UI, no API key needed
    python -m fakturama_automation assets/order-image.png \
        --extractor fixture --fixture tests/fixtures/order-image.json --dry-run

    # the real thing, against a running Fakturama
    python -m fakturama_automation assets/order-image.png --extractor llm --attach

Exit codes are meaningful: 0 success, 2 stopped for manual review, 1 a defect.
A supervising process can tell "a human must look at this" apart from "this is
broken" without parsing the log.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from .errors import AutomationError, ExtractionError, ManualReviewRequired
from .extraction import get_extractor
from .logging_setup import configure_logging
from .models import OrderDocument

log = logging.getLogger("fakturama_automation")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_MANUAL_REVIEW = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fakturama_automation",
        description="Turn an order image into a saved, verified Fakturama Order and Invoice.",
    )
    parser.add_argument("image", type=Path, help="the source order image")

    extraction = parser.add_argument_group("extraction")
    extraction.add_argument(
        "--extractor",
        choices=("llm", "ocr", "fixture"),
        default="llm",
        help="llm: vision model (default) · ocr: Tesseract fallback · fixture: replay saved JSON",
    )
    extraction.add_argument("--fixture", type=Path, help="transcription JSON for --extractor fixture")
    extraction.add_argument("--model", help="override the vision model id")
    extraction.add_argument(
        "--save-extraction", type=Path, help="write the extracted document to JSON for later replay"
    )

    target = parser.add_argument_group("target application")
    target.add_argument("--attach", action="store_true", help="use an already-running Fakturama")
    target.add_argument("--launch", action="store_true", help="start Fakturama first")
    target.add_argument("--exe", help="path to Fakturama.exe (else FAKTURAMA_EXE, else autodetect)")
    target.add_argument(
        "--window", default="Fakturama", help="substring of the main window title (default: Fakturama)"
    )
    target.add_argument(
        "--allow-any-process",
        action="store_true",
        help="accept a title match even when the owning process does not look like Fakturama "
        "(off by default: a title-only match can select a browser showing this repo)",
    )

    run = parser.add_argument_group("run")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="extract and validate only; do not touch the UI",
    )
    run.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="proceed even if the external reference is already booked",
    )
    run.add_argument(
        "--artifacts",
        type=Path,
        default=Path("artifacts"),
        help="directory for screenshots and the run log (default: artifacts/)",
    )
    run.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def summarise(doc: OrderDocument) -> str:
    lines = [
        f"  reference   {doc.external_reference}   date {doc.order_date}   {doc.currency}",
        f"  debtor      {doc.debtor.company} / {doc.debtor.first_name} {doc.debtor.last_name}"
        + (f" / alias {doc.debtor.alias}" if doc.debtor.alias else ""),
        f"  billing     {doc.debtor.billing_address.street}, "
        f"{doc.debtor.billing_address.zip} {doc.debtor.billing_address.city}",
        f"  delivery    {doc.debtor.delivery_address.street}, "
        f"{doc.debtor.delivery_address.zip} {doc.debtor.delivery_address.city}"
        + ("  (same as billing)" if doc.debtor.delivery_equals_billing else "  (separate address)"),
        f"  payment     {doc.payment.method.value} -> {doc.payment.method.fakturama_code}"
        f"   {doc.payment.status.value}"
        + (f" on {doc.payment.payment_date}" if doc.payment.payment_date else ""),
        f"  VAT records {', '.join(doc.vat_names)}",
        "  items",
    ]
    for item in doc.items:
        lines.append(
            f"    {item.position}. {item.sku:<14} {item.description:<24} "
            f"qty {item.quantity} x {item.unit_net_price} "
            f"- {item.discount_percent}% = {item.computed_line_net}"
            f"   (product master gross {item.product_gross_price})"
        )
    lines.append(
        f"  totals      net {doc.totals.net}   VAT {doc.totals.vat}   gross {doc.totals.gross}"
    )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    artifacts = args.artifacts
    configure_logging(artifacts, verbose=args.verbose)

    if not args.image.is_file():
        log.error("image not found: %s", args.image)
        return EXIT_ERROR

    # ---------------------------------------------------------------- extract
    try:
        extractor = get_extractor(args.extractor, fixture_path=args.fixture, model=args.model)
        log.info("extracting %s using the %s backend", args.image.name, extractor.name)
        doc = extractor.extract(args.image)
    except ExtractionError as exc:
        log.error("extraction failed: %s", exc)
        return EXIT_ERROR

    print("extracted and reconciled:\n" + summarise(doc))

    if args.save_extraction:
        args.save_extraction.parent.mkdir(parents=True, exist_ok=True)
        args.save_extraction.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        log.info("wrote %s", args.save_extraction)

    if args.dry_run:
        log.info("dry run: stopping before touching the UI")
        return EXIT_OK

    # ------------------------------------------------------------------- drive
    if not (args.attach or args.launch):
        log.error("choose --attach (use a running Fakturama) or --launch (start it)")
        return EXIT_ERROR

    from .flow.runner import run_flow
    from .uia.backend import Screenshotter, Session, resolve_executable

    shots = Screenshotter(artifacts / "screenshots")
    try:
        if args.launch:
            executable = resolve_executable(args.exe)
            if executable is None:
                log.error(
                    "could not find Fakturama.exe — pass --exe, set FAKTURAMA_EXE, "
                    "or install from https://www.fakturama.info/download/"
                )
                return EXIT_ERROR
            session = Session.launch(
                executable, shots, window_hint=args.window, allow_any_process=args.allow_any_process
            )
        else:
            session = Session.attach(
                shots, window_hint=args.window, allow_any_process=args.allow_any_process
            )

        run_flow(session, doc, skip_duplicate_check=args.allow_duplicate)
    except ManualReviewRequired as exc:
        log.error("STOPPED FOR MANUAL REVIEW — %s", exc)
        print(f"\nstopped for manual review at step {exc.step}: {exc.reason}", file=sys.stderr)
        if exc.screenshot:
            print(f"screenshot: {exc.screenshot}", file=sys.stderr)
        return EXIT_MANUAL_REVIEW
    except AutomationError as exc:
        log.exception("automation failed: %s", exc)
        return EXIT_ERROR

    print(f"\ndone. artifacts in {artifacts.resolve()}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
