"""What the agent pays to have something analysed.

Research is free to the agent today. It is handed a watchlist and analyses all
of it, so "what is worth looking at" is not a decision it makes and not a
decision anyone can grade. Charging for it changes that, and it also makes the
question this app exists to answer honest: whether the model earns its keep
should include the cost of running the model.

**Off by default.** The price is a setting and zero means no charge, so the
live deployment behaves exactly as it did. The experiment deployment turns it
on. That matters while a model comparison is running next door: a research
charge would change the agent's cash, and two variables moving at once make
neither result clean.

The price is deliberately not the measured cost. At one round an analysis is
about $0.05 on a paid vendor and $0.003 in local electricity — a factor of
seventeen — so a single price is **a choice about scarcity**, not a
passthrough. $0.05 charges roughly what the vendor path costs.
"""
import datetime
import logging
import os

from backend.database import db

log = logging.getLogger("ten-acre.research")

_PRICE_SETTING_KEY = "research_price_usd"

# What one analysis costs the agent. Roughly what a vendor charges for the same
# work, so the constraint is real rather than invented: at 12 tickers it is
# $0.60 a morning against a $10,000 book, about a 1.5% annual hurdle.
#
# The charge is the whole point of letting the agent choose what to research.
# Free research is just a longer watchlist, and an agent that pays nothing for
# being wrong about what was worth studying learns nothing from being wrong.
DEFAULT_PRICE = 0.05


def _default_price() -> float:
    """The price the app starts at, before anyone sets one.

    ``RESEARCH_PRICE_USD`` overrides it. Zero is accepted and makes research
    free, which is worth being able to say deliberately — but it is no longer
    the default, so a fresh container charges from its first analysis rather
    than running free until somebody remembers.
    """
    raw = (os.environ.get("RESEARCH_PRICE_USD") or "").strip()
    if not raw:
        return DEFAULT_PRICE
    try:
        return max(0.0, float(raw))
    except ValueError:
        log.warning("Ignoring unparseable RESEARCH_PRICE_USD %r", raw)
        return DEFAULT_PRICE


def get_price() -> float:
    stored = db.get_setting(_PRICE_SETTING_KEY)
    try:
        return max(0.0, float(stored)) if stored else _default_price()
    except (TypeError, ValueError):
        log.warning("Ignoring unparseable %s %r", _PRICE_SETTING_KEY, stored)
        return _default_price()


def set_price(usd: float) -> None:
    if usd < 0:
        raise ValueError("A research price cannot be negative.")
    db.set_setting(_PRICE_SETTING_KEY, str(usd))


def is_charging() -> bool:
    return get_price() > 0


def charge(ticker: str, note: str | None = None) -> int | None:
    """Bill the agent for one analysis. Returns the charge id, or None.

    Charged whether or not the analysis produced a signal. The work was done
    either way, and research you paid for and learned nothing from is the
    normal case rather than an accounting error.

    Never raises. A charge that fails must not undo an analysis that already
    ran — the alternative is a book that is wrong in the direction of thinking
    it has more money than it does.
    """
    price = get_price()
    if price <= 0:
        return None
    try:
        return db.record_research_charge(
            ticker=ticker.upper().strip(),
            amount_usd=price,
            charged_at=datetime.datetime.now(datetime.timezone.utc),
            note=note,
        )
    except Exception:
        log.exception("Could not record the research charge for %s", ticker)
        return None


def total_spent() -> float:
    """Everything spent on research so far, for the book's cash calculation."""
    return sum(row.amount_usd for row in db.get_research_charges())


def spent_by_day() -> dict[datetime.date, float]:
    """Research spend per day, so the equity curve can carry it."""
    out: dict[datetime.date, float] = {}
    for row in db.get_research_charges():
        day = row.charged_at.date()
        out[day] = out.get(day, 0.0) + row.amount_usd
    return out
