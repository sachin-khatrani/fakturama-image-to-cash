"""Live UIA tree inspector.

The grounding strategy is only as good as its assumptions about the real widget
tree, and those assumptions cannot be made from a screenshot. This dumps what
Fakturama actually exposes — control types, names, AutomationIds, patterns — so
locators are written against observed structure rather than guessed structure.

It also answers the one question that decides how item lines are driven: does the
Items table expose per-cell elements, or is it a single canvas?

    python -m fakturama_automation.uia.inspector --window Fakturama --depth 12
    python -m fakturama_automation.uia.inspector --probe-grid
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

import uiautomation as auto

from .locators import _name_of, _rect, _type_name, descendants

PATTERN_PROBES = (
    ("Invoke", "GetInvokePattern"),
    ("Value", "GetValuePattern"),
    ("Selection", "GetSelectionPattern"),
    ("SelectionItem", "GetSelectionItemPattern"),
    ("ExpandCollapse", "GetExpandCollapsePattern"),
    ("Grid", "GetGridPattern"),
    ("Table", "GetTablePattern"),
    ("Text", "GetTextPattern"),
    ("Toggle", "GetTogglePattern"),
    ("LegacyIAccessible", "GetLegacyIAccessiblePattern"),
)


def patterns_of(control: auto.Control) -> list[str]:
    available = []
    for label, getter in PATTERN_PROBES:
        accessor = getattr(control, getter, None)
        if accessor is None:
            continue
        try:
            if accessor() is not None:
                available.append(label)
        except Exception:  # noqa: BLE001 - unsupported pattern
            continue
    return available


def describe(control: auto.Control, depth: int) -> str:
    rect = _rect(control)
    geometry = f"{rect.width()}x{rect.height()}" if rect else "?"
    try:
        automation_id = control.AutomationId or ""
    except Exception:  # noqa: BLE001
        automation_id = ""
    try:
        class_name = control.ClassName or ""
    except Exception:  # noqa: BLE001
        class_name = ""
    bits = [f"{'  ' * depth}{_type_name(control) or '?'}"]
    name = _name_of(control).strip()
    if name:
        bits.append(f"name={name[:60]!r}")
    if automation_id:
        bits.append(f"id={automation_id!r}")
    if class_name:
        bits.append(f"class={class_name}")
    bits.append(f"[{geometry}]")
    found = patterns_of(control)
    if found:
        bits.append("{" + ",".join(found) + "}")
    return " ".join(bits)


def find_window(hint: str, allow_any_process: bool = False) -> Optional[auto.Control]:
    """Locate the window to inspect, using the same rule the automation uses.

    Sharing `Session._find_window` matters more here than it looks. Matching on
    the title alone, this tool happily dumped a browser showing the project's own
    repository page — and a tree dump of the wrong application is worse than no
    dump at all, because the locators written from it look researched.
    """
    from .backend import Session

    return Session._find_window(hint, allow_any_process)


def dump(root: auto.Control, max_depth: int, out=sys.stdout) -> int:
    print(describe(root, 0), file=out)
    count = 1
    frontier = [(root, 0)]
    while frontier:
        control, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        try:
            children = control.GetChildren()
        except Exception:  # noqa: BLE001
            continue
        for child in children:
            print(describe(child, depth + 1), file=out)
            count += 1
            frontier.append((child, depth + 1))
    return count


def probe_grid(root: auto.Control, max_depth: int = 14) -> None:
    """Report whether any table-like control exposes cells.

    A DataGrid/Table/List with Grid or Table patterns can be driven element by
    element. A large Custom or Pane control with no children and no patterns is a
    canvas-rendered grid — it must be driven by keyboard navigation plus OCR of
    the cell rectangle instead. Knowing which one applies is the difference
    between a working item-line driver and a rewrite.
    """
    print("scanning for grid-like controls...\n")
    interesting = ("Table", "DataGrid", "List", "Tree", "Custom", "Pane")
    for control in descendants(root, max_depth):
        type_name = _type_name(control)
        if type_name not in interesting:
            continue
        rect = _rect(control)
        if rect is None or rect.width() < 300 or rect.height() < 100:
            continue
        found = patterns_of(control)
        try:
            child_count = len(control.GetChildren())
        except Exception:  # noqa: BLE001
            child_count = -1
        verdict = (
            "element-addressable"
            if ({"Grid", "Table", "Selection"} & set(found)) or child_count > 3
            else "CANVAS — needs keyboard + OCR"
        )
        print(
            f"{type_name:10} name={_name_of(control)[:30]!r:32} "
            f"{rect.width()}x{rect.height()} children={child_count} "
            f"patterns={','.join(found) or '-':40} -> {verdict}"
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Dump the live UIA tree of a window.")
    parser.add_argument("--window", default="Fakturama", help="substring of the window title")
    parser.add_argument("--depth", type=int, default=12, help="maximum tree depth")
    parser.add_argument("--probe-grid", action="store_true", help="classify table-like controls")
    parser.add_argument("--output", help="write the dump to a file instead of stdout")
    parser.add_argument(
        "--allow-any-process",
        action="store_true",
        help="inspect a title match even when its process does not look like Fakturama",
    )
    args = parser.parse_args(argv)

    window = find_window(args.window, args.allow_any_process)
    if window is None:
        print(
            f"no Fakturama window whose title contains {args.window!r} is open.\n"
            "Start Fakturama first. If it is running and this still fails, its process "
            "may be named unexpectedly — re-run with --allow-any-process.",
            file=sys.stderr,
        )
        return 2

    if args.probe_grid:
        probe_grid(window, args.depth)
        return 0

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            count = dump(window, args.depth, handle)
        print(f"wrote {count} elements to {args.output}")
    else:
        count = dump(window, args.depth)
        print(f"\n{count} elements", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
