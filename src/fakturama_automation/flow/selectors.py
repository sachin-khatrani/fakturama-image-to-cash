"""The resolve-or-create pattern, implemented once.

Every master-data step in the specification has the same shape:

    search the selector  ->  classify the result
        exactly one exact match  -> select it, OK
        several / conflicting    -> STOP for manual review
        none                     -> Cancel, create it, come back, search again

The classification is deliberately strict. Fuzzy matching is used only to
*detect* a near-miss and route it to a human — never to accept one. Accepting a
close-enough Debtor puts a real invoice in front of the wrong customer, which is
a far worse outcome than a run that stops and asks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import uiautomation as auto

from ..errors import ManualReviewRequired
from ..uia.backend import Session
from ..uia.locators import Locator, descendants
from ..uia.waits import wait_until_stable
from . import ui

log = logging.getLogger(__name__)


@dataclass
class Row:
    """One result row, as text plus the element to select."""

    text: str
    cells: tuple[str, ...]
    control: auto.Control

    def contains_all(self, values: list[str]) -> bool:
        """True when every required value appears in this row.

        Compared against the row's own cells where they are available, and
        against the flattened row text otherwise — a selector that exposes no
        per-cell elements still exposes the row's accessible name.
        """
        haystack = " | ".join(self.cells) if self.cells else self.text
        folded = haystack.casefold()
        return all(value.strip().casefold() in folded for value in values if value and value.strip())


class SelectorDialog:
    """A Fakturama 'Select the …' dialog."""

    def __init__(self, session: Session, title: str) -> None:
        self.session = session
        self.title = title
        self.window = session.wait_for_window(title)
        self._scope_resolver_to_dialog()

    def _scope_resolver_to_dialog(self) -> None:
        from ..uia.locators import Resolver

        self._outer_resolver = self.session.resolver
        self.session.resolver = Resolver(self.window)

    def _restore_resolver(self) -> None:
        self.session.resolver = self._outer_resolver
        self.session.invalidate()

    # ------------------------------------------------------------------ search

    def search(self, term: str) -> list[Row]:
        """Type a search term and wait for the list to stop changing.

        The wait matters more than it looks. Reading the list while it is still
        repopulating returns a premature empty result, which would send the flow
        into the creation branch and duplicate a record that already exists.
        """
        try:
            self.session.set_text(ui.DIALOG_SEARCH, term, verify=False)
        except Exception as exc:  # noqa: BLE001 - some dialogs filter as you type only
            log.debug("could not type into the dialog search box: %s", exc)

        wait_until_stable(lambda: self._row_signature(), f"{self.title!r} results for {term!r}")
        rows = self._read_rows()
        log.info("%s: %r matched %d row(s)", self.title, term, len(rows))
        return rows

    def _row_signature(self) -> tuple[str, ...]:
        return tuple(row.text for row in self._read_rows())

    def _read_rows(self) -> list[Row]:
        table = None
        for locator in (ui.DIALOG_RESULTS, ui.DIALOG_RESULTS_ALT):
            table = self.session.resolver.try_resolve(locator)
            if table is not None:
                break
        if table is None:
            return []
        return read_rows(table)

    # ------------------------------------------------------------------ actions

    def select(self, row: Row) -> None:
        try:
            pattern = row.control.GetSelectionItemPattern()
            if pattern is not None:
                pattern.Select()
            else:
                row.control.Click(simulateMove=False)
        except Exception:  # noqa: BLE001
            row.control.Click(simulateMove=False)
        self.session.click(ui.DIALOG_OK)
        self._restore_resolver()
        self.session.wait_for_window_closed(self.title)

    def cancel(self) -> None:
        self.session.click(ui.DIALOG_CANCEL)
        self._restore_resolver()
        self.session.wait_for_window_closed(self.title)


def read_rows(table: auto.Control) -> list[Row]:
    """Read a result table into rows, however it happens to be built.

    Prefers real row elements (DataItem / ListItem / TreeItem) and their cell
    children. Falls back to the row's accessible name when no cells are exposed.
    """
    rows: list[Row] = []
    for candidate in descendants(table, 4):
        try:
            type_name = (candidate.ControlTypeName or "").replace("Control", "")
            if type_name not in ("DataItem", "ListItem", "TreeItem"):
                continue
            cells = tuple(
                (child.Name or "").strip()
                for child in descendants(candidate, 2)
                if (child.ControlTypeName or "").replace("Control", "") in ("Text", "Edit", "DataItem")
                and (child.Name or "").strip()
            )
            text = (candidate.Name or "").strip() or " | ".join(cells)
            if not text and not cells:
                continue
            rows.append(Row(text=text, cells=cells, control=candidate))
        except Exception:  # noqa: BLE001 - a row can disappear mid-read
            continue
    return rows


def resolve_or_create(
    session: Session,
    *,
    step: str,
    dialog_title: str,
    search_term: str,
    is_exact: Callable[[Row], bool],
    create: Callable[[], None],
    entity: str,
) -> None:
    """Run the full select-or-create branch for one master-data record.

    On the second pass the record must be selectable — successfully selecting it
    from the Order *is* the proof that it saved. If it is still not there, the
    creation silently failed and a human needs to look.
    """
    dialog = SelectorDialog(session, dialog_title)
    rows = dialog.search(search_term)
    matches = [row for row in rows if is_exact(row)]

    if len(matches) == 1:
        log.info("%s: selected existing %s %r", step, entity, search_term)
        dialog.select(matches[0])
        return

    if len(matches) > 1:
        shot = session.shot(f"{step}-ambiguous")
        dialog.cancel()
        raise ManualReviewRequired(
            step,
            f"{len(matches)} rows match {entity} {search_term!r} exactly; cannot choose",
            observed=[row.text for row in matches],
            screenshot=str(shot) if shot else None,
        )

    near = [row for row in rows if row.contains_all([search_term])]
    if near:
        # Something that looks like it but is not an exact match. Not a reason to
        # create a duplicate, and not a reason to accept it either.
        shot = session.shot(f"{step}-near-miss")
        dialog.cancel()
        raise ManualReviewRequired(
            step,
            f"{entity} {search_term!r} has near-matches that are not exact",
            expected=search_term,
            observed=[row.text for row in near],
            screenshot=str(shot) if shot else None,
        )

    log.info("%s: no exact %s for %r, creating it", step, entity, search_term)
    dialog.cancel()
    create()

    dialog = SelectorDialog(session, dialog_title)
    rows = dialog.search(search_term)
    matches = [row for row in rows if is_exact(row)]
    if len(matches) != 1:
        shot = session.shot(f"{step}-not-selectable-after-create")
        dialog.cancel()
        raise ManualReviewRequired(
            step,
            f"newly created {entity} {search_term!r} is not selectable from the Order "
            f"({len(matches)} exact matches) — it may not have saved",
            observed=[row.text for row in rows],
            screenshot=str(shot) if shot else None,
        )
    dialog.select(matches[0])
    log.info("%s: created and selected %s %r", step, entity, search_term)


def find_list_row(session: Session, table_locator: Locator, required: list[str]) -> Optional[Row]:
    """Find a single row in a plain list view (Data > VATs, Data > Documents…)."""
    table = session.resolver.try_resolve(table_locator)
    if table is None:
        return None
    for row in read_rows(table):
        if row.contains_all(required):
            return row
    return None
