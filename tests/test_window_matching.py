"""Regression tests for target-window selection.

Matching a window on its title alone is dangerous: any browser tab, editor or
terminal whose title mentions Fakturama matches the substring, and the
automation would then type into it. This was not hypothetical — with this
project's own GitHub page open, a title-only match selected Google Chrome.

These tests pin the behaviour that prevents it.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("uiautomation", reason="UI Automation is Windows-only")

if sys.platform != "win32":  # pragma: no cover - the suite targets Windows
    pytest.skip("Windows only", allow_module_level=True)

from fakturama_automation.uia import backend  # noqa: E402
from fakturama_automation.uia.backend import Session  # noqa: E402


class FakeWindow:
    ControlTypeName = "WindowControl"

    def __init__(self, name: str, process: str) -> None:
        self.Name = name
        self._process = process


class FakeRoot:
    def __init__(self, children):
        self._children = children

    def GetChildren(self):
        return self._children


@pytest.fixture
def desktop(monkeypatch):
    """Install a fake desktop and a process-name lookup driven by the fixture."""

    def install(*windows: FakeWindow):
        monkeypatch.setattr(backend.auto, "GetRootControl", lambda: FakeRoot(list(windows)))
        monkeypatch.setattr(backend, "_process_name", lambda control: control._process)
        return windows

    return install


CHROME = FakeWindow(
    "fakturama-image-to-cash/docs at main - sachin-khatrani - Google Chrome", "chrome.exe"
)
EDITOR = FakeWindow("backend.py - fakturama-image-to-cash - Visual Studio Code", "code.exe")
REAL = FakeWindow("Fakturama 2.1.2 - company data", "fakturama.exe")
JVM = FakeWindow("Fakturama 2.1.2 - company data", "javaw.exe")
UNKNOWN = FakeWindow("Fakturama something", "mystery.exe")


def test_a_browser_showing_this_repo_is_not_the_target(desktop):
    """The bug this test exists for: --attach selecting Chrome."""
    desktop(CHROME)
    assert Session._find_window("Fakturama") is None


def test_an_editor_with_the_project_open_is_not_the_target(desktop):
    desktop(EDITOR)
    assert Session._find_window("Fakturama") is None


def test_the_real_application_is_found(desktop):
    desktop(REAL)
    assert Session._find_window("Fakturama") is REAL


def test_the_application_is_found_when_it_runs_under_a_jvm(desktop):
    desktop(JVM)
    assert Session._find_window("Fakturama") is JVM


def test_the_application_wins_over_a_decoy_regardless_of_order(desktop):
    desktop(CHROME, REAL)
    assert Session._find_window("Fakturama") is REAL
    desktop(REAL, CHROME)
    assert Session._find_window("Fakturama") is REAL


def test_the_real_application_wins_over_a_jvm_when_both_match(desktop):
    desktop(JVM, REAL)
    assert Session._find_window("Fakturama") is REAL


def test_an_unrecognised_process_is_refused_unless_overridden(desktop):
    desktop(UNKNOWN)
    assert Session._find_window("Fakturama") is None
    assert Session._find_window("Fakturama", allow_any_process=True) is UNKNOWN


def test_the_override_still_refuses_nothing_but_is_explicit(desktop):
    """--allow-any-process is an escape hatch, so it may select a browser.

    That is the point of it being a flag: the dangerous behaviour is reachable
    only when a person asks for it by name.
    """
    desktop(CHROME)
    assert Session._find_window("Fakturama", allow_any_process=True) is CHROME


def test_a_window_whose_title_does_not_match_is_never_returned(desktop):
    desktop(FakeWindow("Calculator", "fakturama.exe"))
    assert Session._find_window("Fakturama") is None
