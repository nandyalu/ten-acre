"""The whole prompt, frozen, so a refactor of `build_prompt` can be proved.

`build_prompt` is one long function that appends to a single list of lines, and
it is about to be split into named sections. A test that asserts the prompt
*contains* something cannot tell whether a section moved, lost a blank line, or
picked up a divider it should not have. This one compares the entire rendered
prompt against a stored file, so any change at all shows up as a diff.

**These files are a baseline, not a specification.** Nothing here says the
prompt is right — `agent-probes.md` holds that evidence, and only the model's
own reasoning can produce it. What these files say is: this is exactly what the
prompt was before you touched it. When a change is deliberate, regenerate them
and read the diff.

    REGEN_PROMPTS=1 uv run pytest backend/tests/test_the_prompt_sections.py

The nine fixtures exist to reach every conditional branch in the function. A
branch with no fixture is a branch a refactor can break silently.

Everything that moves on its own is pinned: the clock, the database reads, the
conviction floor, the memory notes, and the day a holding is counted from.
"""
import datetime
import os
import types
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.services import agent, agent_book

GOLDEN = Path(__file__).parent / "prompts"
ET = ZoneInfo("America/New_York")

# Monday, mid-session. The user-facing clock line reads "closes in 4h 40m".
OPEN_NOW = datetime.datetime(2026, 9, 21, 11, 19, tzinfo=ET)
# Saturday, so `market_clock.describe` takes its weekend branch.
SHUT_NOW = datetime.datetime(2026, 9, 19, 18, 5, tzinfo=ET)


# --- the inputs, all fixed ------------------------------------------------------


def _holding(ticker, quantity, avg_cost, price, opened):
    return agent_book.Holding(
        ticker=ticker, quantity=quantity, avg_cost=avg_cost, price=price,
        opened=datetime.date.fromisoformat(opened),
    )


def _book(cash=1_200.0, budget=10_000.0, realized=150.25, holdings=None):
    return agent_book.Book(
        budget=budget, cash=cash, realized_pnl=realized, holdings=holdings or []
    )


def _signal(ticker, at, decision, **extra):
    fields = dict(
        id=1, ticker=ticker, signal_date=at[:10], created_at=at, decision=decision,
        price_at_signal=None, entry_price=None, stop_loss=None, price_target=None,
        win_probability=None, risk_reward=None, expected_value_r=None, trigger=None,
    )
    fields.update(extra)
    return types.SimpleNamespace(**fields)


def _closed(ticker, entry, exit_price, days, decision=None, quantity=4):
    return agent_book.TradeRow(
        ticker=ticker, quantity=quantity, entry=entry,
        entry_at=datetime.datetime(2026, 9, 8, 14, 30),
        exit=exit_price,
        exit_at=datetime.datetime(2026, 9, 8, 14, 30) + datetime.timedelta(days=days),
        signal_decision=decision,
    )


def _candidate(ticker, name, price, change_pct, volume_m):
    return types.SimpleNamespace(
        ticker=ticker, name=name, price=price, change_pct=change_pct,
        volume_m=volume_m, source="most active",
    )


def _pending(ticker, side, quantity, limit_price, placed_at):
    return types.SimpleNamespace(
        ticker=ticker, side=side, quantity=quantity, limit_price=limit_price,
        is_stop=False, placed_at=datetime.datetime.fromisoformat(placed_at),
    )


def _exit(kind, price):
    return types.SimpleNamespace(exit_kind=kind, limit_price=price)


HOLDINGS = [
    _holding("AAPL", 12, 220.50, 231.10, "2026-09-15"),
    _holding("NVDA", 5, 170.00, 165.25, "2026-09-18"),
]
PRICES = {"AAPL": 231.10, "NVDA": 165.25, "INTC": 24.80, "ORCL": 291.40}
PRICE_RANGES = {"AAPL": (215.20, 236.40), "NVDA": (160.10, 178.90)}
DAY_RANGES = {
    "AAPL": (228.90, 233.05), "NVDA": (163.70, 171.20),
    "INTC": (24.10, 25.60), "ORCL": (288.00, 294.75),
}
RESTING = {"AAPL": [_exit("stop", 210.00), _exit("target", 250.00)], "NVDA": []}

SIGNALS = [
    _signal(
        "INTC", "2026-09-21 13:05:00", "Buy", price_at_signal=24.10, entry_price=24.35,
        stop_loss=22.90, price_target=28.60, win_probability=62.0, risk_reward=2.9,
        expected_value_r=0.84, trigger="commissioned",
    ),
    _signal(
        "ORCL", "2026-09-18 17:40:00", "Hold", price_at_signal=286.20, entry_price=289.00,
        stop_loss=274.50, price_target=318.00, win_probability=51.0, risk_reward=2.0,
        trigger="move",
    ),
]
CLOSED = [
    _closed("AVGO", 330.00, 352.40, 6, decision="Buy"),
    _closed("HOOD", 96.20, 91.05, 3, decision="Hold"),
]
MENU = [
    _candidate("SOFI", "SoFi Technologies Inc", 27.35, 1.8, 42.6),
    _candidate("PLTR", "Palantir Technologies Inc", 174.90, -2.1, 61.3),
]
ALERTS = [
    {"at": "21 Sep 10:42 AM", "text": "NVDA fell 7.9% to $165.25 on 2.4x normal volume"},
    {"at": "21 Sep 9:48 AM", "text": "AAPL reached its target of $230.00"},
]
FAILURES = [
    {"side": "buy", "quantity": 12, "ticker": "AAPL",
     "why": "the broker refused the order: unsettled funds"},
]
WAKEUPS = [
    {"at": "21 Sep 9:30 AM", "acted": True},
    {"at": "21 Sep 10:15 AM", "acted": False},
    {"at": "21 Sep 11:00 AM", "acted": False},
]
CHANGES = [{"date": "2026-09-20", "message": "An adjust now names the level it replaced."}]
MEMORY = ["Skip INTC — stopped out on it twice this month on the same breakout setup."]
PENDING = [_pending("TSLA", "buy", 3, 402.50, "2026-09-19 15:41:00")]
RUNNING = {"ORCL": datetime.datetime(2026, 9, 21, 15, 12, tzinfo=datetime.timezone.utc)}

FULL = dict(
    book=_book(holdings=HOLDINGS),
    signals=SIGNALS,
    prices=PRICES,
    closed=CLOSED,
    regime_line="Market regime: VIX 14.2, SPY 4.1% above its 200-day average, yield curve normal.",
    horizon_days=10,
    menu=MENU,
    price=0.05,
    watchlist=["AAPL", "INTC", "NVDA", "ORCL"],
    max_watchlist=30,
    failures=FAILURES,
    unsettled_cash=412.80,
    wakeups=WAKEUPS,
    analysis_minutes=[9.4, 10.1, 11.8],
    running_analyses=RUNNING,
    changes=CHANGES,
    alerts=ALERTS,
    earnings=[("ORCL", "2026-09-24")],
    price_ranges=PRICE_RANGES,
    day_ranges=DAY_RANGES,
)


# --- the nine prompts -----------------------------------------------------------


def _quiet():
    """Nothing held, nothing analysed, nothing noticed. The shortest prompt.

    The only fixture with no pending order and no memory note, so the empty
    path through both is covered too.
    """
    return dict(
        pending=[], memory=[],
        kwargs=dict(
            book=_book(cash=10_000.0, realized=0.0, holdings=[]), signals=[], prices={}
        ),
    )


def _full():
    """Every optional section present at once."""
    return dict(kwargs=dict(FULL))


def _woken():
    """Something was noticed while the agent slept, so the pointer fires."""
    return dict(kwargs=dict(
        FULL,
        woke_because="A rule watching your tickers spotted something. You did not ask for this pass.",
        last_pass_notes=["Waiting to see whether NVDA holds $170 after the open."],
    ))


def _early():
    """A pass earlier than the wakeup the agent planned for itself."""
    return dict(kwargs=dict(
        FULL,
        woke_because="A change to this app woke you. You did not ask for this pass.",
        wakeup_note="Ruled out HPE at a $59.83 entry, worth another look under $56.",
        planned_wakeup=datetime.datetime(2026, 9, 23, 9, 30, tzinfo=ET),
    ))


def _turn2():
    """The second turn of a pass, after research the agent ordered landed."""
    return dict(kwargs=dict(
        FULL,
        outcomes=["Analysis of INTC finished. It decided Buy.", "Bought 12 AAPL at $231.10."],
        researched_now={"INTC"},
        pass_notes=["INTC came back Buy at a 62% chance; sizing off the $22.90 stop."],
    ))


def _asked_again():
    """All four reasons to be asked again in one prompt, so all four pointers fire."""
    return dict(kwargs=dict(
        FULL,
        outcomes=["Analysis of INTC finished. It decided Buy."],
        researched_now={"INTC"},
        # The embedded rule is what `_joined` strips: an analyst wrote one a
        # third of the way into a real read, and every section is separated
        # by one now.
        readings=[
            "INTC 2026-09-21 — Rating: Buy.\n\n---\n\n"
            "## Risk Assessment\n\nThe app computed the entry, stop and target.\n\n"
            "### Probability\n\n62%."
        ],
        dropped_with_read=[{"ticker": "NVDA", "side": "sell", "quantity": 5}],
        rejected=[
            agent_book.Rejection(
                ticker="INTC", side="buy", quantity=80,
                why="costs $1,984.00 against $1,200.00 of cash",
            )
        ],
        woke_because="You asked to be woken now.",
    ))


def _tool():
    """The Gemini channel: no menu, a one-line watchlist, and `decide` at the end."""
    return dict(kwargs=dict(FULL, answer_by_tool=True))


def _broke():
    """Cash below the research price, so the budget rule is replaced."""
    return dict(kwargs=dict(FULL, book=_book(cash=-8.00, holdings=HOLDINGS)))


def _closed_session():
    """A Saturday, with a conviction floor set.

    Two rare branches in one file rather than two: the market-closed rule only
    renders when the session is shut, and the floor defaults to off.
    """
    return dict(now=SHUT_NOW, open_session=False, conviction=(55.0, 2.0),
                kwargs=dict(FULL))


PROMPTS = {
    "quiet": _quiet,
    "full": _full,
    "woken": _woken,
    "early": _early,
    "turn2": _turn2,
    "asked_again": _asked_again,
    "tool": _tool,
    "broke": _broke,
    "closed": _closed_session,
}


# --- the pin --------------------------------------------------------------------


@pytest.fixture
def pinned(monkeypatch):
    """Freeze everything `build_prompt` reads that is not one of its arguments.

    Returns a function the test calls with the fixture's own settings, because
    one of the nine needs a shut market and one needs an empty database.
    """
    def pin(now=OPEN_NOW, open_session=True, conviction=(0.0, 0.0),
            pending=None, memory=None):
        now_utc = now.astimezone(datetime.timezone.utc)
        # Every clock call funnels through `now_et`, including `describe`,
        # `next_open` and `minutes_to_close`. An argument still converts, so
        # a signal's `created_at` renders as its real Eastern time.
        monkeypatch.setattr(
            agent.market_clock, "now_et",
            lambda at=None: now if at is None else at.astimezone(ET),
        )
        monkeypatch.setattr(agent.watchdog, "is_us_market_hours", lambda: open_session)
        # `describe_analysis_timing` takes its own `now`, and `build_prompt`
        # never passes one — so it reads the real wall clock and the "N min so
        # far" line moves every minute. The renderer still runs for real here;
        # only its clock is fixed. The first regeneration caught this: nine
        # baselines drifted a minute apart without a line of code changing.
        timing = agent.describe_analysis_timing
        monkeypatch.setattr(
            agent, "describe_analysis_timing",
            lambda durations, running, now=None: timing(durations, running, now=now or now_utc),
        )
        # `Holding.held_days` defaults to today's real date, so the holdings
        # table would drift by a day every day without this.
        monkeypatch.setattr(
            agent_book.Holding, "held_days",
            lambda self, today=None: None if self.opened is None else (now.date() - self.opened).days,
        )
        monkeypatch.setattr(agent.db, "get_resting_exits", lambda ticker: RESTING.get(ticker, []))
        orders = PENDING if pending is None else pending
        monkeypatch.setattr(agent.db, "get_pending_agent_trades", lambda: list(orders))
        monkeypatch.setattr(
            agent.db, "get_recent_signals",
            lambda ticker=None, limit=10: [s for s in SIGNALS if s.ticker == ticker],
        )
        notes = MEMORY if memory is None else memory
        monkeypatch.setattr(agent, "get_memory_notes", lambda: list(notes))
        monkeypatch.setattr(agent, "get_conviction", lambda: conviction)
    return pin


# --- the test -------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_the_prompt_is_what_it_was(name, pinned):
    fixture = PROMPTS[name]()
    pinned(**{k: v for k, v in fixture.items() if k != "kwargs"})

    built = agent.build_prompt(**fixture["kwargs"])

    path = GOLDEN / f"{name}.txt"
    if os.environ.get("REGEN_PROMPTS"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(built)
        pytest.skip(f"regenerated {path.name}")

    assert path.exists(), f"missing baseline: run REGEN_PROMPTS=1 to write {path.name}"
    assert built == path.read_text(), (
        f"{name} changed. If that was deliberate, regenerate with REGEN_PROMPTS=1 "
        "and read the diff before committing it."
    )


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_a_rule_always_means_a_section_boundary(name, pinned):
    """Sections embed the analysts' own writing, and analysts write horizontal
    rules. One inside a read made the read look finished and the paragraph
    after it look like a new section."""
    fixture = PROMPTS[name]()
    pinned(**{k: v for k, v in fixture.items() if k != "kwargs"})

    built = agent.build_prompt(**fixture["kwargs"])
    sections = built.split("\n---\n")

    assert "\n---\n" not in sections[0], "the opening line is not a section"
    for i, section in enumerate(sections[1:], 1):
        assert section.lstrip().startswith("## "), (
            f"{name}: rule {i} is not followed by a heading, so something "
            f"embedded a rule of its own: {section.lstrip()[:80]!r}"
        )
        # Only the section's own title may sit at `##`, and nothing at `#`.
        # 60 of the 126 stored analyst reports carry a `## ` heading, so an
        # embedded one would read as this section having ended.
        body = section.lstrip().split("\n", 1)[1] if "\n" in section.lstrip() else ""
        stolen = [l for l in body.splitlines() if l.startswith("# ") or l.startswith("## ")]
        assert not stolen, f"{name}: embedded heading at section level: {stolen[:2]}"


def test_every_prompt_fixture_has_a_baseline():
    """A fixture with no stored file would pass its own test by skipping."""
    missing = [name for name in PROMPTS if not (GOLDEN / f"{name}.txt").exists()]
    assert not missing, f"no baseline for: {', '.join(missing)}"
