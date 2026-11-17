"""Unit tests for the robot-config scripts. No hardware, no nmcli, no sudo.

    uv run --with pytest==8.3.4 --with pyyaml==6.0.2 pytest tests -q

The scripts are uv inline scripts, not a package, so tests import them by path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load_script(name: str) -> ModuleType:
    """Import scripts/<name>.py as a fresh module."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module
