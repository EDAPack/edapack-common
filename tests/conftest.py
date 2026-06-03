"""Shared test helpers: load the hyphenated CLI scripts as modules."""

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(stem: str):
    """Import scripts/<stem>.py (handles hyphenated filenames)."""
    path = SCRIPTS / f"{stem}.py"
    mod_name = stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # needed for dataclass module introspection
    spec.loader.exec_module(mod)
    return mod
