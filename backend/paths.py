"""Where the app keeps what it writes.

The database, the logs, the journey files and the public snapshot all live in
one data directory. One folder holds everything a deployment owns, so a backup
of that folder is complete. Three rules pick the folder, in this order:

1. ``TEN_ACRE_DATA_DIR``, when it is set. The Dockerfile sets it to
   ``/app/data``, the named volume.
2. ``<repo>/data``, when a ``pyproject.toml`` sits beside the ``backend``
   package. That is a checkout, and a checkout keeps its data beside the code,
   as it always has.
3. ``~/.local/share/ten-acre``, the per-user data home. That is an installed
   copy, from ``uv tool install``. Its code folder is replaced on every upgrade,
   so it must hold nothing of the user's.

Rule 3 is why this module exists. Before 2026-09-17 every path was computed two
folders above its own file. Inside an installed package that lands in
``site-packages``, and the next upgrade deletes it.
"""
import os
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent  # backend/
_CHECKOUT_MARKER = _PACKAGE_DIR.parent / "pyproject.toml"


def data_dir() -> Path:
    """The directory the app writes to. The module docstring has the rules."""
    configured = os.environ.get("TEN_ACRE_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if _CHECKOUT_MARKER.is_file():
        return _PACKAGE_DIR.parent / "data"
    xdg_home = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(xdg_home).expanduser() / "ten-acre"
