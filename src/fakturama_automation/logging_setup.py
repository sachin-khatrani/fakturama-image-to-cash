"""Run logging.

Every run writes a full debug log next to its screenshots, so a failure can be
reconstructed after the fact without reproducing it. The console stays readable;
the file keeps everything.
"""

from __future__ import annotations

import logging
from pathlib import Path

CONSOLE_FORMAT = "%(levelname)-7s %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(artifacts: Path, verbose: bool = False) -> Path:
    artifacts.mkdir(parents=True, exist_ok=True)
    log_path = artifacts / "run.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root.addHandler(console)

    to_file = logging.FileHandler(log_path, encoding="utf-8")
    to_file.setLevel(logging.DEBUG)
    to_file.setFormatter(logging.Formatter(FILE_FORMAT))
    root.addHandler(to_file)

    # comtypes logs every COM call at DEBUG; it drowns everything else out.
    logging.getLogger("comtypes").setLevel(logging.WARNING)
    return log_path
