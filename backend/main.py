"""Entrypoint: loads the environment, brings the database schema up to date,
then runs the FastAPI app (backend/app.py) with uvicorn. FastAPI owns the
process and the event loop; the quiv scheduler and the optional Discord client
both start and stop inside its lifespan (see backend/app.py).

``main()`` is the one start sequence for every way of running the app: the
``ten-acre`` command an installed copy gets, and ``python -m backend.main`` in
the container and in a checkout. The migration used to be a second command in
the Docker entrypoint. An installed copy has no entrypoint script to put it in,
so it moved here, and the container runs the same code.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from backend import paths
from backend.services import logsetup

_PACKAGE_DIR = Path(__file__).resolve().parent
_ALEMBIC_INI = _PACKAGE_DIR / "alembic.ini"


def _load_env() -> None:
    """A checkout's .env first, then the data directory's.

    The first is the repo root's .env, the file this app has always read. An
    installed copy has no file there, and reads the .env in its data directory
    instead. The order matters: a checkout's .env may be the file that sets
    TEN_ACRE_DATA_DIR. A value already in the environment wins over both files,
    which is how the container gets everything from compose. A missing file is
    skipped in silence.
    """
    load_dotenv(_PACKAGE_DIR.parent / ".env")
    load_dotenv(paths.data_dir() / ".env")


def _migrate() -> None:
    """Bring the database to the current schema. Idempotent: a start with
    nothing to do only confirms the database is at head.

    A subprocess and not alembic's Python API, so alembic's own logging setup
    (the [loggers] section of alembic.ini, applied by env.py) never touches
    this process's handlers, and a failed migration stops the start with the
    same non-zero exit the old two-command entrypoint gave.
    """
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(_ALEMBIC_INI), "upgrade", "head"],
        check=True,
    )


def main() -> None:
    _load_env()
    logging.basicConfig(level=logging.INFO)
    # The data directory may not exist yet on a first start. SQLite creates the
    # file but not the folder above it.
    paths.data_dir().mkdir(parents=True, exist_ok=True)
    # Also to a file in the data directory. Docker's log dies with the
    # container, and a run that has been erased cannot explain itself — see
    # backend/services/logsetup.py.
    logsetup.configure()
    _migrate()
    uvicorn.run("backend.app:app", host="0.0.0.0", port=int(os.environ.get("API_PORT", "8080")))


if __name__ == "__main__":
    main()
