"""One-off: delete every signal and everything derived from it, so the
scorecard can start fresh after a change of trade horizon.

Signals recorded under one horizon are evidence about a different question
than signals recorded under another. Mixing a 6-month thesis into a 1-2 week
scorecard makes both unreadable, so the switch to swing starts from empty.

This is deliberately a script and not an Alembic migration: a migration runs
on every deployment, and wiping the signal history on every deployment would
be a disaster. Run it once, by hand::

    python -m backend.scripts.reset_signals            # dry run, counts only
    python -m backend.scripts.reset_signals --yes      # actually delete

What it deletes, in foreign-key order (``PRAGMA foreign_keys=ON`` is set in
backend/database/engine.py, so the order is enforced, not just tidy):

1. ``papertransaction`` — references ``signal.id``
2. ``signalreport`` — references ``signal.id``
3. ``signal``
4. ``papersnapshot`` — no foreign key, but it is the valuation history of the
   paper trades deleted above; leaving it makes the equity curve start with a
   step change out of nowhere.

What it keeps: ``transaction`` (real, broker-synced holdings — actual money),
``watchlistticker``, ``botsetting``, and ``tickerprice`` (the price cache is
horizon-neutral and takes weeks to rebuild). ``alert`` is kept unless
``--include-alerts`` is passed; old alerts are stale but harmless.
"""
import argparse
import sys

from sqlmodel import Session, delete, func, select

from backend.database.engine import write_session
from backend.database.models import (
    Alert,
    Signal,
    SignalReport,
)

# Ordered: children before parents. Do not reorder — foreign keys are on.
_WIPE_ORDER = (SignalReport, Signal)
# The agent's own book is NOT wiped. This script exists to start the signal
# record fresh after a change of horizon; deleting the trades made under the old
# signals would leave the book claiming cash it never had.
_KEEP = ("agenttrade", "agentrun", "researchcharge", "watchlistticker", "botsetting", "tickerprice")


@write_session
def count_rows(models, *, _session: Session = None) -> dict[str, int]:
    return {
        model.__tablename__: _session.exec(select(func.count()).select_from(model)).one()
        for model in models
    }


@write_session
def wipe(models, *, _session: Session = None) -> dict[str, int]:
    """Deletes every row of each model in the order given, in one transaction,
    so a failure part-way leaves the database untouched rather than
    half-cleared."""
    deleted = {}
    for model in models:
        result = _session.exec(delete(model))
        deleted[model.__tablename__] = result.rowcount
    _session.commit()
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually delete; without it the script only reports what it would delete",
    )
    parser.add_argument(
        "--include-alerts",
        action="store_true",
        help="also clear the watchdog alert log",
    )
    args = parser.parse_args()

    models = list(_WIPE_ORDER)
    if args.include_alerts:
        models.append(Alert)

    counts = count_rows(models)
    total = sum(counts.values())

    print("Rows to delete:")
    for table, count in counts.items():
        print(f"  {table:<20} {count:>7}")
    print(f"  {'TOTAL':<20} {total:>7}")
    print("\nKept untouched: " + ", ".join(_KEEP))

    if total == 0:
        print("\nNothing to do.")
        return 0

    if not args.yes:
        print("\nDry run. Re-run with --yes to delete.")
        print("Take a copy of trading.db in the data directory first.")
        return 0

    deleted = wipe(models)
    print("\nDeleted:")
    for table, count in deleted.items():
        print(f"  {table:<20} {count:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
