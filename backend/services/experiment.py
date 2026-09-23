"""When *this* deployment's experiment began.

The date was a constant compiled into the frontend bundle, which is fine while
one container runs and wrong the moment a second one does: every image built
from this repo carried the same date, so a container started today announced
itself as being on day eight of somebody else's experiment.

**Configurable, defaulting to the date already published.** An unset variable
resolves to 2026-09-02, so the original deployment's site does not move and no
page it has ever served changes.

**Deliberately not derived from the data.** The earliest recorded pass in the
live database is 2026-09-03, while the published start is the 2nd, and that gap
is intentional: the container was deployed and switched on that day and the
first pass simply fell after hours. Deriving the date would replace "when this
experiment began" — a claim somebody made on purpose — with "when it first
happened to produce a row", which is an artifact of scheduling.

``intraday.experiment_start()`` reads this too — a floor for how far back to
backfill 1-minute bars. It was a separate constant until 2026-09-10, on the
reasoning that a floor is not a claim about when anything began. True, and
beside the point: a self-hoster starting four months from now would have
paged back four months to reach somebody else's date.
"""
import datetime
import logging
import os

from backend.database import db

log = logging.getLogger("ten-acre.experiment")

# Written once, the first time the agent is switched on, and never overwritten.
_SETTING_KEY = "experiment_started_on"

# The first deployment's start date, and the answer whenever nobody says
# otherwise. See CLAUDE.md: the code that removed every manual control was
# written on 2026-09-01, but nothing was running that day — no container, no
# account, no book. The experiment starts when the agent can act.
DEFAULT_START = datetime.date(2026, 9, 2)


def _stored() -> str | None:
    """The recorded date, or None if the database cannot answer.

    **Defensive on purpose.** A container whose migrations have not run has no
    ``botsetting`` table, and this is read from ``/api/settings`` — which the
    frontend fetches on load, so a raise here would break the whole site for
    exactly the deployment least able to diagnose it. Unreadable reads as
    "nothing recorded", which then falls through to the env var and the
    constant. Same reasoning as ``setup_check._setting``.
    """
    try:
        return db.get_setting(_SETTING_KEY)
    except Exception:
        log.warning("Could not read the recorded start date — falling back")
        return None


def _parse(value: str | None, source: str) -> datetime.date | None:
    """An ISO date, or None with a warning. Never raises: a mistyped date
    should cost a wrong label on one page, not a container's ability to
    start."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        log.warning("%s=%r is not an ISO date (YYYY-MM-DD) — ignoring it", source, value)
        return None


def record_start(today: datetime.date | None = None) -> datetime.date:
    """Stamp today as the start, unless a date is already recorded.

    Called when the agent is switched on. **Write-once** — a deployment that
    is paused and resumed months later has not started a second experiment,
    and a start date that moved would silently rewrite every "day N" and
    every "since" the site has already shown.
    """
    existing = _parse(_stored(), _SETTING_KEY)
    if existing is not None:
        return existing
    # A stated date is what gets stamped. On 2026-09-23 a deployment that had
    # run since 2026-09-03 with no stamp was stamped with the day its settings
    # were next saved, which moved every "day N" its site had shown.
    started = today or _configured() or datetime.date.today()
    db.set_setting(_SETTING_KEY, started.isoformat())
    log.info("Experiment start recorded as %s — the agent was switched on today", started)
    return started


def start_date() -> datetime.date:
    """The day this deployment's experiment began.

    ``EXPERIMENT_START_DATE`` first, then the stored value, then the constant.
    The stored value is written when the agent is first switched on, so a
    self-hoster never has to know any of this exists. The variable is an
    override for a date the stamp cannot know: a deployment restored from a
    backup, or one stamped late. **It beats the stamp since 2026-09-23**, when
    a stamp written on the wrong day could not be corrected by it. The
    constant keeps the original deployment answering what its own site has
    always shown.

    Read per call rather than at import so a test can change it without
    reimporting, matching ``tradeable_account_class`` and
    ``configured_account`` in sandbox_broker.
    """
    return _configured() or _parse(_stored(), _SETTING_KEY) or DEFAULT_START


def _configured() -> datetime.date | None:
    return _parse(os.environ.get("EXPERIMENT_START_DATE"), "EXPERIMENT_START_DATE")


def day_number(today: datetime.date | None = None) -> int:
    """Which day of this experiment today is, counting the first as day 1.

    Counted from the start rather than from the first fill, for the reason the
    frontend has always given: "day 3" should mean three days of the experiment
    running, including the days it chose to do nothing — those are results too,
    and a counter that only starts on the first purchase hides them.
    """
    today = today or datetime.date.today()
    return max(1, (today - start_date()).days + 1)
