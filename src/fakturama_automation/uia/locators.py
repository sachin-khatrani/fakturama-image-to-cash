"""The control-resolution ladder.

A `Locator` declares *what* a control is, never *where* it is. The resolver
tries progressively weaker strategies until one succeeds, and reports every
strategy it tried when none does.

Nothing in this module refers to an absolute screen position. The only geometry
used is the relative position of one element to another — which is how a person
finds an unlabelled text box too, by looking at the label beside it.

The ladder (DESIGN.md §4):
  1. AutomationId                       — rarely available in SWT, free when it is
  2. Accessible name + control type     — named fields and most buttons
  3. Label adjacency                    — the common case for SWT edits
  4. Ordinal within a typed sibling set  — icon-only toolbar controls
Containment scoping (`within=`) composes with all of them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterator, Optional

import uiautomation as auto

from ..errors import ControlNotFound
from .waits import DEFAULT_TIMEOUT, wait_for

log = logging.getLogger(__name__)

# How far a field may sit from its label, as a fraction of the container's own
# width/height. Deliberately generous: forms align fields in a column, so a
# SHORT label sits FURTHER from its field than a long one. Sizing this allowance
# from the label's own dimensions gets that backwards and rejects exactly the
# short labels ("ZIP", "City") that matter. The real disambiguation is done by
# `_closest_label_wins` below, not by the cap.
LABEL_MAX_GAP_FRACTION = 0.6
LABEL_MIN_GAP_ALLOWANCE = 250.0


@dataclass(frozen=True)
class Locator:
    """A semantic description of a control."""

    description: str
    control_type: Optional[str] = None
    name: Optional[str] = None
    name_contains: bool = False
    automation_id: Optional[str] = None
    label: Optional[str] = None
    label_side: str = "auto"  # 'right' | 'below' | 'auto'
    within: Optional["Locator"] = None
    index: Optional[int] = None
    expect_siblings: Optional[int] = None
    max_depth: int = 14
    predicate_names: tuple[str, ...] = field(default=())

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return self.description


# --------------------------------------------------------------------------- #
# tree helpers
# --------------------------------------------------------------------------- #


def _type_name(control: auto.Control) -> str:
    try:
        return (control.ControlTypeName or "").replace("Control", "")
    except Exception:  # noqa: BLE001
        return ""


def _type_matches(control: auto.Control, wanted: Optional[str]) -> bool:
    if not wanted:
        return True
    return _type_name(control).casefold() == wanted.replace("Control", "").casefold()


def _name_of(control: auto.Control) -> str:
    try:
        return control.Name or ""
    except Exception:  # noqa: BLE001
        return ""


def _rect(control: auto.Control):
    try:
        return control.BoundingRectangle
    except Exception:  # noqa: BLE001
        return None


def _visible(control: auto.Control) -> bool:
    rect = _rect(control)
    if rect is None:
        return False
    try:
        return rect.width() > 0 and rect.height() > 0 and not control.IsOffscreen
    except Exception:  # noqa: BLE001
        return rect.width() > 0 and rect.height() > 0


def descendants(root: auto.Control, max_depth: int = 14) -> Iterator[auto.Control]:
    """Breadth-first walk of the subtree. Breadth-first matters: the control a
    person would consider 'the' match is almost always the shallowest one."""
    frontier = [(root, 0)]
    while frontier:
        control, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        try:
            children = control.GetChildren()
        except Exception:  # noqa: BLE001 - a window can vanish mid-walk
            continue
        for child in children:
            yield child
            frontier.append((child, depth + 1))


def _matching(
    scope: auto.Control,
    control_type: Optional[str],
    max_depth: int,
    name: Optional[str] = None,
    name_contains: bool = False,
) -> list[auto.Control]:
    found: list[auto.Control] = []
    for control in descendants(scope, max_depth):
        if not _type_matches(control, control_type):
            continue
        if name is not None:
            actual = _name_of(control).strip()
            if name_contains:
                if name.casefold() not in actual.casefold():
                    continue
            elif actual.casefold() != name.casefold():
                continue
        found.append(control)
    return found


# --------------------------------------------------------------------------- #
# ladder rungs
# --------------------------------------------------------------------------- #


def _by_automation_id(scope: auto.Control, loc: Locator) -> list[auto.Control]:
    if not loc.automation_id:
        return []
    out = []
    for control in descendants(scope, loc.max_depth):
        try:
            if control.AutomationId == loc.automation_id and _type_matches(control, loc.control_type):
                out.append(control)
        except Exception:  # noqa: BLE001
            continue
    return out


def _by_name(scope: auto.Control, loc: Locator) -> list[auto.Control]:
    if loc.name is None:
        return []
    return [
        c
        for c in _matching(scope, loc.control_type, loc.max_depth, loc.name, loc.name_contains)
        if _visible(c)
    ]


def _by_label(scope: auto.Control, loc: Locator) -> list[auto.Control]:
    """Find the field belonging to a printed label.

    Looks for a static-text element whose text matches the label, then picks the
    nearest candidate control to its right (same row) or below it (same column).
    SWT labels a field by drawing text beside it, and nothing else; this rung is
    the reason unnamed edits are reachable at all.
    """
    if not loc.label or loc.index is not None:
        # With an index, the label only anchors the candidate set — `_by_index`
        # owns the choice so the sibling-count assertion still applies.
        return []

    texts = [
        c
        for c in descendants(scope, loc.max_depth)
        if _type_name(c) in ("Text", "Static", "Group") and _visible(c) and _name_of(c).strip()
    ]
    labels = [c for c in texts if _label_text_matches(_name_of(c), loc.label)]
    if not labels:
        return []

    candidates = [c for c in _matching(scope, loc.control_type, loc.max_depth) if _visible(c)]
    if not candidates:
        return []

    scope_rect = _rect(scope)
    max_h_gap = (
        max(scope_rect.width() * LABEL_MAX_GAP_FRACTION, LABEL_MIN_GAP_ALLOWANCE)
        if scope_rect
        else LABEL_MIN_GAP_ALLOWANCE * 2
    )
    max_v_gap = (
        max(scope_rect.height() * LABEL_MAX_GAP_FRACTION, LABEL_MIN_GAP_ALLOWANCE)
        if scope_rect
        else LABEL_MIN_GAP_ALLOWANCE * 2
    )

    best: list[tuple[float, auto.Control]] = []
    for label in labels:
        lrect = _rect(label)
        if lrect is None:
            continue
        for candidate in candidates:
            crect = _rect(candidate)
            if crect is None:
                continue
            distance = _adjacency_distance(lrect, crect, loc.label_side, max_h_gap, max_v_gap)
            if distance is None:
                continue
            if not _closest_label_wins(lrect, crect, texts, distance, loc):
                continue
            best.append((distance, candidate))

    best.sort(key=lambda pair: pair[0])
    ordered: list[auto.Control] = []
    for _, control in best:
        if not any(_same_control(control, seen) for seen in ordered):
            ordered.append(control)
    return ordered


def _closest_label_wins(
    lrect, crect, all_texts: list[auto.Control], distance: float, loc: Locator
) -> bool:
    """Reject a field that clearly belongs to a different label.

    This is what actually makes label adjacency safe on a two-column form. In
    Fakturama's Debtor editor, `Company` and `Contact` sit on the same row; a
    pure nearest-to-the-right rule would happily pair `Company` with the contact
    field once the company field is narrow. A field is only accepted if no other
    label is nearer to it than the one asked for.
    """
    for other in all_texts:
        orect = _rect(other)
        if orect is None or _label_text_matches(_name_of(other), loc.label or ""):
            continue
        if orect.left == lrect.left and orect.top == lrect.top:
            continue
        other_distance = _adjacency_distance(orect, crect, loc.label_side, 1e9, 1e9)
        if other_distance is not None and other_distance < distance - 1:
            return False
    return True


def _label_text_matches(actual: str, wanted: str) -> bool:
    a = actual.strip().rstrip(":*").casefold()
    w = wanted.strip().rstrip(":*").casefold()
    return a == w or (len(w) > 3 and a.startswith(w))


def _adjacency_distance(
    lrect, crect, side: str, max_h_gap: float, max_v_gap: float
) -> Optional[float]:
    """Distance from a label to a candidate field, or None if not adjacent.

    'Adjacent' means same row and to the right, or same column and below —
    with vertical/horizontal overlap, so a field two columns over does not win.
    """
    to_right = (
        crect.left >= lrect.right - 2
        and crect.left - lrect.right <= max_h_gap
        and crect.top < lrect.bottom
        and crect.bottom > lrect.top
    )
    below = (
        crect.top >= lrect.bottom - 2
        and crect.top - lrect.bottom <= max_v_gap
        and crect.left < lrect.right
        and crect.right > lrect.left
    )
    if side == "right" and not to_right:
        return None
    if side == "below" and not below:
        return None
    if side == "auto" and not (to_right or below):
        return None
    if to_right:
        return float(crect.left - lrect.right)
    return float(crect.top - lrect.bottom) + 1000.0  # prefer same-row over below


def _by_index(scope: auto.Control, loc: Locator) -> list[auto.Control]:
    """Nth control of a type, with the sibling count asserted.

    Used for icon-only controls that carry no name — the two selector icons
    beside Addresses, and the pair beside the Items table. The specification
    distinguishes them only by position ("the upper existing-contact icon", "the
    lower green +"), so position is all there is to go on.

    Two things keep that honest. The candidate set is narrowed by a label anchor
    whenever one is given, so the ordinal is taken among the icons *beside
    Addresses* rather than among every button in the window — title-bar buttons
    included. And the sibling count is asserted: if Fakturama gains or loses a
    control here, this refuses to guess instead of silently clicking the wrong
    icon and starting a new Debtor when it meant to select an existing one.
    """
    if loc.index is None:
        return []
    if loc.label:
        siblings = _label_anchored_candidates(scope, loc)
    else:
        siblings = [c for c in _matching(scope, loc.control_type, loc.max_depth) if _visible(c)]
    siblings.sort(key=lambda c: (_rect(c).top, _rect(c).left) if _rect(c) else (0, 0))
    if loc.expect_siblings is not None and len(siblings) != loc.expect_siblings:
        log.warning(
            "%s: expected %d sibling %s controls, found %d — refusing the ordinal",
            loc.description,
            loc.expect_siblings,
            loc.control_type,
            len(siblings),
        )
        return []
    if loc.index < len(siblings):
        return [siblings[loc.index]]
    return []


# Container types whose internal buttons belong to the container, not to the form.
COMPOSITE_WIDGETS = ("ComboBox", "Edit", "Spinner", "ScrollBar", "Slider", "TitleBar")


def _is_part_of_widget(control: auto.Control, scope: auto.Control) -> bool:
    """True when this control is a sub-part of a larger widget.

    A combo box owns a drop-down button, a spinner owns its arrows, a title bar
    owns its close button. Those are nearer to a section label than the icons
    that actually belong to it, so a plain proximity search picks them up and the
    sibling-count assertion then refuses the whole locator. Excluding them is
    what makes "the two icons beside Addresses" mean the two icons beside
    Addresses.
    """
    try:
        parent = control.GetParentControl()
    except Exception:  # noqa: BLE001
        return False
    depth = 0
    while parent is not None and depth < 12:
        try:
            if _same_control(parent, scope):
                return False
            if _type_name(parent) in COMPOSITE_WIDGETS:
                return True
            parent = parent.GetParentControl()
        except Exception:  # noqa: BLE001
            return False
        depth += 1
    return False


def _rect_distance(a, b) -> float:
    """Gap between two rectangles; 0 when they overlap."""
    dx = max(a.left - b.right, b.left - a.right, 0)
    dy = max(a.top - b.bottom, b.top - a.bottom, 0)
    return float((dx * dx + dy * dy) ** 0.5)


def _label_anchored_candidates(scope: auto.Control, loc: Locator) -> list[auto.Control]:
    """Controls of the wanted type clustered around a label.

    Strict row/column adjacency is the right rule for a label and its single
    input, but not for a *stack* of icons beside a section heading: the second
    icon is below-and-right of the label and satisfies neither test. Proximity
    is the honest rule for that shape, so the candidate set is everything of the
    right type within a radius of the label, and ordering picks upper vs lower.
    """
    labels = [
        c
        for c in descendants(scope, loc.max_depth)
        if _type_name(c) in ("Text", "Static", "Group")
        and _visible(c)
        and _label_text_matches(_name_of(c), loc.label or "")
    ]
    if not labels:
        return []

    scope_rect = _rect(scope)
    radius = (
        max(scope_rect.width() * 0.25, LABEL_MIN_GAP_ALLOWANCE)
        if scope_rect
        else LABEL_MIN_GAP_ALLOWANCE
    )

    out: list[auto.Control] = []
    for candidate in _matching(scope, loc.control_type, loc.max_depth):
        if not _visible(candidate) or _is_part_of_widget(candidate, scope):
            continue
        crect = _rect(candidate)
        if crect is None:
            continue
        for label in labels:
            lrect = _rect(label)
            if lrect is not None and _rect_distance(lrect, crect) <= radius:
                if not any(_same_control(candidate, seen) for seen in out):
                    out.append(candidate)
                break
    return out


def _same_control(a: auto.Control, b: auto.Control) -> bool:
    try:
        return a.GetRuntimeId() == b.GetRuntimeId()
    except Exception:  # noqa: BLE001
        return a is b


# --------------------------------------------------------------------------- #
# resolver
# --------------------------------------------------------------------------- #

_RUNGS = (
    ("automation_id", _by_automation_id),
    ("name", _by_name),
    ("label-adjacency", _by_label),
    ("ordinal", _by_index),
)


class Resolver:
    """Resolves locators against a root window, with a per-window cache."""

    def __init__(self, root: auto.Control) -> None:
        self.root = root
        self._cache: dict[Locator, auto.Control] = {}

    def _scope(self, loc: Locator) -> auto.Control:
        if loc.within is None:
            return self.root
        return self.resolve(loc.within)

    def find_all(self, loc: Locator) -> list[auto.Control]:
        scope = self._scope(loc)
        for rung_name, rung in _RUNGS:
            found = rung(scope, loc)
            if found:
                log.debug("%s resolved via %s (%d match)", loc.description, rung_name, len(found))
                return found
        return []

    def try_resolve(self, loc: Locator) -> Optional[auto.Control]:
        cached = self._cache.get(loc)
        if cached is not None:
            try:
                if cached.Exists(0, 0):
                    return cached
            except Exception:  # noqa: BLE001
                pass
            self._cache.pop(loc, None)
        found = self.find_all(loc)
        if not found:
            return None
        self._cache[loc] = found[0]
        return found[0]

    def resolve(self, loc: Locator, timeout: float = DEFAULT_TIMEOUT) -> auto.Control:
        """Resolve or raise, waiting for the control to appear."""
        try:
            return wait_for(lambda: self.try_resolve(loc), f"control {loc.description!r}", timeout)
        except Exception as exc:  # noqa: BLE001 - convert to the domain error
            tried = [name for name, _ in _RUNGS]
            raise ControlNotFound(loc.description, tried) from exc

    def exists(self, loc: Locator, timeout: float = 1.0) -> bool:
        try:
            wait_for(lambda: self.try_resolve(loc), loc.description, timeout, interval=0.15)
            return True
        except Exception:  # noqa: BLE001
            return False

    def invalidate(self) -> None:
        """Drop the cache — call after a window opens, closes or re-lays out."""
        self._cache.clear()
