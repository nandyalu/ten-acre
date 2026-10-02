"""What a stock's own price and volume say about it, as cells the agent reads.

Until 2026-10-01 the agent had no view of a stock's trend or momentum. The
signals table said what the analyst decided and at what price. The watchlist
table said how far the price had moved since. Nothing said whether the stock
was above or below its long averages, whether it had been rising for a month,
or whether anyone was trading it more than usual. To learn that, the agent paid
for an analysis and waited for it, and the answer came as prose. The market
analyst computed exactly these figures on every run (see
``build_verified_market_snapshot`` in the submodule) and the agent never saw
one of them.

**These are facts about the stock, not predictions.** A trained model with a
confidence score was considered and set aside: a per-ticker classifier on five
years of daily bars overfits, and a confidence figure nothing has verified is an
invented number that reads as data. The reasoning is in PLAN.md, under the
rejected section dated 2026-10-01.
A fact the agent can check against the price is a tool. A score it must take on
trust is a second opinion.

**Nothing in Python decides from these.** They are shown and that is all. A
Buy in a DOWN trend is still the agent's call, the same as a Buy on a Hold.

Every read goes through ``bars.get_bars`` over completed sessions, never the
session in progress: a mid-session volume or range understates the day for no
reason but the clock, and the ``Price now`` column already carries today.
``describe`` never raises. A ticker whose history cannot be read gets no cells,
shown as dashes, and the pass goes on.
"""
import datetime
import logging
from dataclasses import dataclass

from backend.services import sizing

log = logging.getLogger("ten-acre.trend")

# Sessions, not calendar days. 21 sessions is about a month of trading and 63
# about a quarter; 50 and 200 are the averages every chart draws. The regime
# line already reads the S&P against its 200-day average.
SHORT_AVERAGE = 50
LONG_AVERAGE = 200
MONTH = 21
QUARTER = 63
VOLUME_RECENT = 5
VOLUME_NORMAL = 60
RANGE_PERIOD = sizing._ATR_PERIOD

# Calendar days of history asked of the cache. 200 sessions is about 290
# calendar days; the rest covers holidays, so a 200-day average does not go
# missing in a week with one.
_HISTORY_DAYS = 330

MISSING = "—"

# A one-session close-to-close ratio outside these bounds is a break in the
# history: a split, a spin-off or a data error, not a move. The bar cache
# stores raw bars, so CTVA read 77.65 on 2026-09-30 and 12.57 on 2026-10-01,
# and the first probe showed the agent "DOWN: 50d -84.3%" as if the company
# had collapsed. A 2:1 split is a ratio of 0.5; the bounds sit just inside
# the smallest common split and the smallest reverse split, so a real 40%
# fall still shows and a split does not. The cells are withheld rather than
# computed across the break, because a wrong figure that reads as data is
# worse than a dash.
_BREAK_LOW = 0.52
_BREAK_HIGH = 1.9

# The column names, in the order every table shows them. One place, so the
# three tables and the legend cannot disagree about what a column is called.
COLUMNS = ("Trend", "1m / 3m", "Volume vs normal", "Range/day")


@dataclass(frozen=True)
class Trend:
    """The cells for one ticker, as of its last completed session.

    A field is None when the history is too short to compute it. A listing
    four months old has a month and a quarter but no 200-day average.
    """

    as_of: str
    close: float
    break_on: str | None
    break_pct: float | None
    vs_short_pct: float | None
    vs_long_pct: float | None
    month_pct: float | None
    quarter_pct: float | None
    volume_ratio: float | None
    range_pct: float | None

    @property
    def word(self) -> str | None:
        """UP above both averages, DOWN below both, MIXED between them.

        The word carries the conclusion. The STALE marker showed that a number
        on a row is read and drawn from only when a word beside it says what
        the number means (JOURNEY.md, 2026-09-22).
        """
        if self.vs_short_pct is None or self.vs_long_pct is None:
            return None
        if self.vs_short_pct > 0 and self.vs_long_pct > 0:
            return "UP"
        if self.vs_short_pct < 0 and self.vs_long_pct < 0:
            return "DOWN"
        return "MIXED"

    def trend_cell(self) -> str:
        if self.break_on is not None:
            day = datetime.date.fromisoformat(self.break_on).strftime("%-d %b")
            return (
                f"withheld: the price history breaks on {day} ({self.break_pct:+.0f}% in one "
                "session, a split or a data error)"
            )
        if self.vs_short_pct is None:
            return MISSING
        if self.vs_long_pct is None:
            return f"{SHORT_AVERAGE}d {self.vs_short_pct:+.1f}%, no {LONG_AVERAGE}-day yet"
        return (
            f"{self.word}: {SHORT_AVERAGE}d {self.vs_short_pct:+.1f}%, "
            f"{LONG_AVERAGE}d {self.vs_long_pct:+.1f}%"
        )

    def returns_cell(self) -> str:
        if self.month_pct is None and self.quarter_pct is None:
            return MISSING
        month = f"{self.month_pct:+.1f}%" if self.month_pct is not None else MISSING
        quarter = f"{self.quarter_pct:+.1f}%" if self.quarter_pct is not None else MISSING
        return f"{month} / {quarter}"

    def volume_cell(self) -> str:
        if self.volume_ratio is None:
            return MISSING
        return f"{self.volume_ratio:.1f}× normal"

    def range_cell(self) -> str:
        if self.range_pct is None:
            return MISSING
        return f"{self.range_pct:.1f}%"

    def cells(self) -> tuple[str, str, str, str]:
        return self.trend_cell(), self.returns_cell(), self.volume_cell(), self.range_cell()


def cells(trend: "Trend | None") -> tuple[str, str, str, str]:
    """The four cells for a row, dashes when the ticker has no trend."""
    if trend is None:
        return (MISSING,) * 4
    return trend.cells()


def legend() -> str:
    """One paragraph that explains the four columns, shared by every table.

    One function, because the same cells sit in three tables and on two
    channels, and a legend that differs between them is a legend that lies in
    one of them. The last sentence says what a withheld row means. The legend
    does not tie Range/day to the Stop: it did at first, and the first probe
    showed that overclaimed (agent-probes.md, 2026-10-01).
    """
    return (
        f"**{COLUMNS[0]}**, **{COLUMNS[1]}**, **{COLUMNS[2]}** and **{COLUMNS[3]}** are "
        "computed by the app from completed sessions, as of the last close, and describe "
        f"the stock rather than any trade. **{COLUMNS[0]}** is the close against its "
        f"{SHORT_AVERAGE}-day and {LONG_AVERAGE}-day averages: UP is above both, DOWN is "
        f"below both, MIXED is between them. **{COLUMNS[1]}** is the price change over the "
        f"last {MONTH} and {QUARTER} sessions. **{COLUMNS[2]}** is the average volume of "
        f"the last {VOLUME_RECENT} sessions against the average of the last {VOLUME_NORMAL}. "
        f"**{COLUMNS[3]}** is the average true range over {RANGE_PERIOD} sessions as a share "
        "of the price: how far the stock moves on an ordinary day. A row whose price "
        "history breaks in one session, a split or a data error, says so and shows no "
        "figures."
    )


def _pct(now: float, then: float) -> float | None:
    if then is None or then <= 0:
        return None
    return (now - then) / then * 100


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _break(history: list) -> tuple[str, float] | None:
    """The first one-session break inside the window the cells use, or None."""
    window = history[-(LONG_AVERAGE + 1):]
    for before, bar in zip(window[:-1], window[1:]):
        if not before.close or bar.close is None:
            continue
        ratio = bar.close / before.close
        if ratio < _BREAK_LOW or ratio > _BREAK_HIGH:
            return bar.date, (ratio - 1) * 100
    return None


def compute(history: list) -> "Trend | None":
    """The cells from a list of completed daily bars, oldest first.

    Pure. Each figure is None when the bars run out before its window does,
    and the whole thing is None only when there is no last close to describe.
    """
    closes = [bar.close for bar in history if bar.close is not None]
    if not closes:
        return None
    close = closes[-1]
    broken = _break(history)
    if broken is not None:
        return Trend(
            as_of=history[-1].date, close=close, break_on=broken[0], break_pct=broken[1],
            vs_short_pct=None, vs_long_pct=None, month_pct=None, quarter_pct=None,
            volume_ratio=None, range_pct=None,
        )
    volumes = [bar.volume for bar in history if bar.volume is not None]

    def average(window: int) -> float | None:
        return _mean(closes[-window:]) if len(closes) >= window else None

    def change(sessions: int) -> float | None:
        # The close `sessions` sessions ago, so a 21-session change spans 21
        # moves. One short and the window is not there yet.
        return _pct(close, closes[-sessions - 1]) if len(closes) > sessions else None

    volume_ratio = None
    if len(volumes) >= VOLUME_NORMAL:
        normal = _mean(volumes[-VOLUME_NORMAL:])
        recent = _mean(volumes[-VOLUME_RECENT:])
        if normal and recent is not None:
            volume_ratio = recent / normal

    range_pct = None
    if len(history) > RANGE_PERIOD:
        atr = sizing.compute_atr(
            [bar.high for bar in history],
            [bar.low for bar in history],
            [bar.close for bar in history],
            RANGE_PERIOD,
        )
        if atr is not None and close > 0:
            range_pct = atr / close * 100

    return Trend(
        as_of=history[-1].date,
        close=close,
        break_on=None,
        break_pct=None,
        vs_short_pct=_pct(close, average(SHORT_AVERAGE)),
        vs_long_pct=_pct(close, average(LONG_AVERAGE)),
        month_pct=change(MONTH),
        quarter_pct=change(QUARTER),
        volume_ratio=volume_ratio,
        range_pct=range_pct,
    )


def describe(ticker: str, today: datetime.date | None = None) -> "Trend | None":
    """The cells for one ticker from the bar cache. Never raises.

    A cold ticker costs one vendor request, the same as any other first read of
    its history; after that the cache answers. Today's session is left out on
    purpose, see the module docstring.
    """
    from backend.services import bars  # lazy: bars imports widely

    today = today or datetime.date.today()
    try:
        history = bars.get_bars(ticker, today - datetime.timedelta(days=_HISTORY_DAYS), today=today)
    except Exception:
        log.warning("Could not read the history of %s for its trend cells", ticker, exc_info=True)
        return None
    try:
        return compute(history)
    except Exception:
        log.exception("Could not compute the trend cells of %s", ticker)
        return None


def describe_many(tickers, today: datetime.date | None = None) -> dict[str, Trend]:
    """One ``describe`` per distinct ticker; a ticker with no trend is absent."""
    found: dict[str, Trend] = {}
    for ticker in sorted({str(t).upper().strip() for t in tickers if t}):
        trend = describe(ticker, today)
        if trend is not None:
            found[ticker] = trend
    return found
