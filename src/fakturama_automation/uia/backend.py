"""The Windows-facing layer: attaching to Fakturama, acting, and reading back.

Two rules are enforced here rather than left to callers:

* **Never click a raw point.** Actions go through the element's own UIA pattern
  (Invoke / SelectionItem / ExpandCollapse). Where no pattern exists, the click
  goes to the centre of the rectangle *that element reports*, after scrolling it
  into view — so it follows the control when the window moves or the DPI changes.

* **Every write is read back.** `set_text` re-reads the field and raises if the
  value did not stick. A silently rejected write is the most common way an
  automation books the wrong document while reporting success.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import uiautomation as auto

from ..errors import AutomationError, ControlNotFound, VerificationError
from .locators import Locator, Resolver, descendants
from .waits import DEFAULT_TIMEOUT, wait_for, wait_gone

log = logging.getLogger(__name__)

FAKTURAMA_WINDOW_HINT = "Fakturama"


@dataclass
class Screenshotter:
    """Per-run artifact capture, annotated as it goes.

    The annotation is not decoration. At capture time the automation already
    knows which element it acted on and why, so drawing that rectangle and
    caption onto the image costs nothing and produces a screenshot that explains
    itself — which is what makes a failed run reviewable without reproducing it.
    """

    directory: Path
    counter: int = 0

    def capture(
        self,
        control: Optional[auto.Control],
        label: str,
        highlight: Optional[auto.Control] = None,
        caption: Optional[str] = None,
    ) -> Optional[Path]:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.counter += 1
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)[:60]
        path = self.directory / f"{self.counter:03d}-{safe}.png"
        try:
            target = control if control is not None else auto.GetRootControl()
            target.CaptureToImage(str(path))
        except Exception as exc:  # noqa: BLE001 - never fail a run over a screenshot
            log.debug("screenshot %s failed: %s", label, exc)
            return None

        try:
            self._annotate(path, target, highlight, caption or label.replace("-", " "))
        except Exception as exc:  # noqa: BLE001 - annotation is best-effort
            log.debug("annotating %s failed: %s", path.name, exc)
        return path

    @staticmethod
    def _annotate(
        path: Path,
        captured: auto.Control,
        highlight: Optional[auto.Control],
        caption: str,
    ) -> None:
        from PIL import Image, ImageDraw

        origin = captured.BoundingRectangle
        with Image.open(path) as source:
            image = source.convert("RGB")
        draw = ImageDraw.Draw(image)

        if highlight is not None:
            rect = highlight.BoundingRectangle
            # Translate from screen coordinates into the captured image.
            box = (
                rect.left - origin.left,
                rect.top - origin.top,
                rect.right - origin.left,
                rect.bottom - origin.top,
            )
            if box[2] > box[0] and box[3] > box[1]:
                draw.rectangle(box, outline=(220, 30, 30), width=3)

        bar_height = 26
        draw.rectangle((0, 0, image.width, bar_height), fill=(20, 30, 45))
        draw.text((8, 6), caption[:160], fill=(255, 255, 255))
        image.save(path)


class Session:
    """A live Fakturama session."""

    def __init__(self, window: auto.WindowControl, screenshotter: Screenshotter) -> None:
        self.window = window
        self.resolver = Resolver(window)
        self.shots = screenshotter

    # ---------------------------------------------------------------- lifecycle

    @classmethod
    def attach(
        cls,
        screenshotter: Screenshotter,
        window_hint: str = FAKTURAMA_WINDOW_HINT,
        timeout: float = 30.0,
    ) -> "Session":
        """Attach to an already-running Fakturama."""
        window = wait_for(
            lambda: cls._find_window(window_hint),
            f"a top-level window whose title contains {window_hint!r}",
            timeout,
        )
        window.SetActive()
        log.info("attached to window: %r", window.Name)
        return cls(window, screenshotter)

    @classmethod
    def launch(
        cls,
        executable: Path,
        screenshotter: Screenshotter,
        window_hint: str = FAKTURAMA_WINDOW_HINT,
        timeout: float = 180.0,
    ) -> "Session":
        """Start Fakturama and wait for its shell window.

        The timeout is generous on purpose: Fakturama is a JVM application and a
        cold start behind an on-access virus scanner is genuinely slow.
        """
        if not executable.is_file():
            raise AutomationError(f"Fakturama executable not found: {executable}")
        log.info("launching %s", executable)
        subprocess.Popen([str(executable)], cwd=str(executable.parent))
        return cls.attach(screenshotter, window_hint, timeout)

    @staticmethod
    def _find_window(hint: str) -> Optional[auto.WindowControl]:
        root = auto.GetRootControl()
        for child in root.GetChildren():
            try:
                name = child.Name or ""
            except Exception:  # noqa: BLE001
                continue
            if hint.casefold() in name.casefold() and child.ControlTypeName == "WindowControl":
                return child
        return None

    # ------------------------------------------------------------- diagnostics

    def shot(
        self,
        label: str,
        highlight: Optional[Locator] = None,
        caption: Optional[str] = None,
    ) -> Optional[Path]:
        """Capture the window, optionally ringing the control this step acted on."""
        control = None
        if highlight is not None:
            control = self.resolver.try_resolve(highlight)
        return self.shots.capture(self.window, label, highlight=control, caption=caption)

    # ---------------------------------------------------------------- resolving

    def find(self, loc: Locator, timeout: float = DEFAULT_TIMEOUT) -> auto.Control:
        return self.resolver.resolve(loc, timeout)

    def find_all(self, loc: Locator) -> list[auto.Control]:
        return self.resolver.find_all(loc)

    def exists(self, loc: Locator, timeout: float = 1.0) -> bool:
        return self.resolver.exists(loc, timeout)

    def invalidate(self) -> None:
        self.resolver.invalidate()

    # ------------------------------------------------------------------ actions

    def click(self, loc: Locator, timeout: float = DEFAULT_TIMEOUT) -> auto.Control:
        control = self.find(loc, timeout)
        self._activate(control, loc.description)
        return control

    def _activate(self, control: auto.Control, description: str) -> None:
        self._bring_into_view(control)
        for pattern_getter, invoke in (
            ("GetInvokePattern", lambda p: p.Invoke()),
            ("GetSelectionItemPattern", lambda p: p.Select()),
            ("GetTogglePattern", lambda p: p.Toggle()),
        ):
            getter = getattr(control, pattern_getter, None)
            if getter is None:
                continue
            try:
                pattern = getter()
            except Exception:  # noqa: BLE001 - pattern unsupported
                continue
            if pattern is None:
                continue
            try:
                invoke(pattern)
                log.debug("%s: activated via %s", description, pattern_getter)
                return
            except Exception as exc:  # noqa: BLE001
                log.debug("%s: %s failed (%s), trying next", description, pattern_getter, exc)
        # No usable pattern. Click the centre of the element's *own* rectangle.
        try:
            control.Click(simulateMove=False)
            log.debug("%s: activated via element-rect click", description)
        except Exception as exc:  # noqa: BLE001
            raise AutomationError(f"could not activate {description}: {exc}") from exc

    @staticmethod
    def _bring_into_view(control: auto.Control) -> None:
        try:
            pattern = control.GetScrollItemPattern()
            if pattern is not None:
                pattern.ScrollIntoView()
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            control.SetFocus()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------- reading and writing

    @staticmethod
    def read(control: auto.Control) -> str:
        """Read a control's value, trying each pattern SWT might expose."""
        for getter, extract in (
            ("GetValuePattern", lambda p: p.Value),
            ("GetLegacyIAccessiblePattern", lambda p: p.Value),
            ("GetTextPattern", lambda p: p.DocumentRange.GetText(-1)),
        ):
            accessor = getattr(control, getter, None)
            if accessor is None:
                continue
            try:
                pattern = accessor()
                if pattern is None:
                    continue
                value = extract(pattern)
            except Exception:  # noqa: BLE001
                continue
            if value is not None:
                return str(value)
        try:
            return control.Name or ""
        except Exception:  # noqa: BLE001
            return ""

    def get_text(self, loc: Locator) -> str:
        return self.read(self.find(loc))

    def set_text(self, loc: Locator, value: str, verify: bool = True, attempts: int = 2) -> None:
        """Write a field and confirm it took.

        Tries the Value pattern first, then clear-and-type. Both paths end at the
        same read-back check, so the caller is told the truth either way.

        A mismatch is retried once from a verified-empty field before it is
        raised. Observed failure mode: a write occasionally lands on top of
        residual text instead of replacing it, producing a value like
        'haBerlin' — plausible enough to pass a human's glance and wrong in the
        saved record. Retrying from empty fixes the transient; still-wrong after
        the retry is a real fault and is raised.
        """
        control = self.find(loc)
        self._bring_into_view(control)
        text = str(value)
        last_actual = ""

        for attempt in range(1, attempts + 1):
            if not (
                self._set_via_value_pattern(control, text) or self._set_via_keyboard(control, text)
            ):
                raise AutomationError(f"could not write to {loc.description}")
            if not verify:
                return
            last_actual = self.read(control).strip()
            if _equivalent(last_actual, text):
                log.debug("%s = %r", loc.description, text)
                return
            if attempt < attempts:
                log.warning(
                    "%s read back as %r after writing %r; clearing and retrying",
                    loc.description,
                    last_actual,
                    text,
                )
                self._clear(control)
        raise VerificationError(loc.description, text, last_actual)

    def _clear(self, control: auto.Control) -> None:
        """Empty a field and confirm it is empty."""
        if not self._set_via_value_pattern(control, ""):
            try:
                control.SetFocus()
                auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.05)
            except Exception:  # noqa: BLE001
                return
        remaining = self.read(control).strip()
        if remaining:
            log.debug("field still holds %r after clearing", remaining)

    @staticmethod
    def _set_via_value_pattern(control: auto.Control, text: str) -> bool:
        try:
            pattern = control.GetValuePattern()
            if pattern is None or pattern.IsReadOnly:
                return False
            pattern.SetValue(text)
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _set_via_keyboard(control: auto.Control, text: str) -> bool:
        try:
            control.SetFocus()
            auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.03)
            auto.SendKeys(_escape_sendkeys(text), waitTime=0.01)
            # Commit the edit: SWT often validates on focus loss.
            auto.SendKeys("{Tab}", waitTime=0.05)
            return True
        except Exception:  # noqa: BLE001
            return False

    def select_option(self, loc: Locator, option: str) -> None:
        """Choose an entry in a combo/dropdown, then verify the selection.

        Never types the option blind. A dropped-down list selects on *each*
        keystroke, so typing "Credit transfer" walks the list letter by letter
        and settles on whatever the last character matched — which is how you
        silently book a SEPA direct debit when you asked for a credit transfer.
        The item is located as an element and selected through its own pattern.
        """
        combo = self.find(loc)
        self._bring_into_view(combo)
        self._expand(combo, True)

        item = self._find_option(combo, option)
        if item is not None:
            try:
                selection = item.GetSelectionItemPattern()
                if selection is not None:
                    selection.Select()
                else:
                    item.Click(simulateMove=False)
            except Exception as exc:  # noqa: BLE001
                raise AutomationError(f"could not select {option!r} in {loc.description}: {exc}") from exc
        elif not self._set_via_value_pattern(combo, option):
            self._expand(combo, False)
            raise ControlNotFound(
                f"option {option!r} in {loc.description}",
                ["popup list items", "value pattern"],
            )

        self._expand(combo, False)
        actual = self.read(combo).strip()
        if not _equivalent(actual, option):
            raise VerificationError(loc.description, option, actual)

    @staticmethod
    def _expand(combo: auto.Control, expand: bool) -> None:
        try:
            pattern = combo.GetExpandCollapsePattern()
            if pattern is None:
                return
            pattern.Expand() if expand else pattern.Collapse()
            time.sleep(0.15)  # let the popup materialise before it is searched
        except Exception:  # noqa: BLE001 - not every combo is expandable
            pass

    @staticmethod
    def _find_option(combo: auto.Control, option: str) -> Optional[auto.Control]:
        """Locate a dropdown entry.

        The popup is frequently NOT a child of the combo — Win32 and SWT both
        host it in a separate top-level window — so the search widens from the
        combo, to its owning window, to the desktop.
        """
        wanted = option.strip().casefold()

        def scan(scope: auto.Control, depth: int) -> Optional[auto.Control]:
            for candidate in descendants(scope, depth):
                try:
                    type_name = (candidate.ControlTypeName or "").replace("Control", "")
                    if type_name not in ("ListItem", "MenuItem", "TreeItem", "DataItem", "Text"):
                        continue
                    if (candidate.Name or "").strip().casefold() == wanted:
                        return candidate
                except Exception:  # noqa: BLE001
                    continue
            return None

        scopes: list[tuple[auto.Control, int]] = [(combo, 4)]
        try:
            top = combo.GetTopLevelControl()
            if top is not None:
                scopes.append((top, 8))
        except Exception:  # noqa: BLE001
            pass
        scopes.append((auto.GetRootControl(), 4))

        for scope, depth in scopes:
            found = scan(scope, depth)
            if found is not None:
                return found
        return None

    # ------------------------------------------------------------------ dialogs

    def wait_for_window(self, title_contains: str, timeout: float = DEFAULT_TIMEOUT) -> auto.WindowControl:
        """Wait for a dialog to appear, by title fragment."""

        def look() -> Optional[auto.WindowControl]:
            for child in auto.GetRootControl().GetChildren():
                try:
                    if title_contains.casefold() in (child.Name or "").casefold():
                        return child
                except Exception:  # noqa: BLE001
                    continue
            for child in descendants(self.window, 3):
                try:
                    if child.ControlTypeName == "WindowControl" and title_contains.casefold() in (
                        child.Name or ""
                    ).casefold():
                        return child
                except Exception:  # noqa: BLE001
                    continue
            return None

        window = wait_for(look, f"window titled like {title_contains!r}", timeout)
        self.invalidate()
        return window

    def wait_for_window_closed(self, title_contains: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        def still_open() -> bool:
            for child in auto.GetRootControl().GetChildren():
                try:
                    if title_contains.casefold() in (child.Name or "").casefold():
                        return True
                except Exception:  # noqa: BLE001
                    continue
            return False

        wait_gone(still_open, f"window titled like {title_contains!r}", timeout)
        self.invalidate()


def _escape_sendkeys(text: str) -> str:
    """uiautomation treats {} as key syntax; escape any literal braces."""
    return text.replace("{", "{{}").replace("}", "{}}")


def _equivalent(actual: str, expected: str) -> bool:
    """Compare leniently enough to survive the UI's own formatting.

    Fakturama re-formats what it is given — a date typed as 2026-07-14 may read
    back as 14.07.2026, and '0' may read back as '0,00'. Comparing raw strings
    would fail on a correct write, so compare on digits when both sides are
    numeric-looking, and on collapsed whitespace otherwise.
    """
    a, b = actual.strip(), expected.strip()
    if a.casefold() == b.casefold():
        return True
    a_digits = "".join(ch for ch in a if ch.isdigit())
    b_digits = "".join(ch for ch in b if ch.isdigit())
    if a_digits and b_digits:
        if a_digits == b_digits:
            return True
        # 250 vs 250,00 — trailing zeros added by the UI's own formatting.
        if a_digits.rstrip("0") == b_digits.rstrip("0") and abs(len(a_digits) - len(b_digits)) <= 2:
            return True
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def resolve_executable(explicit: Optional[str] = None) -> Optional[Path]:
    """Locate Fakturama.exe: explicit path, env var, then the usual install roots."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    from_env = os.environ.get("FAKTURAMA_EXE")
    if from_env:
        candidates.append(Path(from_env))
    for root in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
    ):
        if not root or not root.exists():
            continue
        for entry in root.glob("Fakturama*"):
            candidates.extend(entry.glob("Fakturama*.exe"))
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None
