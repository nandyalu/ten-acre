"""Key figures about a company, for the agent's ``fundamentals`` fetch.

Webull has no filter screener: its screens are rank lists (most active,
gainers, sectors). So "screen by PE or PEG", which the agent asked for on
2026-09-17, is two steps: fetch the rank lists (``candidates``), then ask for
each name's figures. This is the second step.

**The figures come from Yahoo Finance, the rating from Webull (2026-09-19).**
The first version read Webull's financial indicators and industry
comparison, and both answered "404 Route Not Found" on every call: the
sandbox host has no route for the ``/openapi/fundamentals/`` family. The
analyst rating lives under ``/openapi/instrument/`` and works. yfinance is
what the analysis pipeline reads its fundamentals from, so the agent now sees
the source its analysts see: the ratios in ``Ticker.info`` and the last four
quarters of the income statement. The industry comparison has no equivalent
there and is gone. The profile names the sector and industry instead, and
nothing is ranked.

**Rendered as tables, and nothing is invented.** A value is printed as the
vendor sent it, with no unit added. The margins, returns and growth rates in
``info`` are fractions of one, and the header says so. A block that fails
or comes back empty says so in one line.

Blocking (HTTP). The agent's pass already runs on a worker thread, and
``agent.ToolContext`` keeps the result for the pass, so a second call for the
same ticker does not reach Yahoo again.
"""
import logging
import math

import yfinance as yf
from tradingagents.dataflows.stockstats_utils import yf_retry

from backend.services import quotes

log = logging.getLogger("ten-acre.fundamentals")

# The ``info`` keys shown, with the name the model reads, in table order.
# They are the keys the fundamentals analyst prints, without the price
# averages the prompt's own tables already carry, and without the dividend
# yield, whose scale Yahoo has changed before.
_RATIOS = [
    ("marketCap", "Market cap"),
    ("trailingPE", "PE, trailing"),
    ("forwardPE", "PE, forward"),
    ("pegRatio", "PEG"),
    ("priceToBook", "Price to book"),
    ("priceToSalesTrailing12Months", "Price to sales, trailing"),
    ("trailingEps", "EPS, trailing"),
    ("forwardEps", "EPS, forward"),
    ("profitMargins", "Profit margin"),
    ("operatingMargins", "Operating margin"),
    ("returnOnEquity", "Return on equity"),
    ("returnOnAssets", "Return on assets"),
    ("debtToEquity", "Debt to equity"),
    ("currentRatio", "Current ratio"),
    ("freeCashflow", "Free cash flow"),
    ("revenueGrowth", "Revenue growth, year on year"),
    ("earningsGrowth", "Earnings growth, year on year"),
    ("beta", "Beta"),
]
# The income-statement rows shown, by the name yfinance gives them.
_QUARTER_ROWS = [
    ("Total Revenue", "Revenue"),
    ("Gross Profit", "Gross profit"),
    ("Operating Income", "Operating income"),
    ("Net Income", "Net income"),
    ("Diluted EPS", "EPS, diluted"),
]
# Quarters shown, newest first. Four is a year of trend; more is a wider
# table the model would skim.
_QUARTERS = 4


def describe(ticker: str, client=None) -> str:
    """The three blocks for one ticker, as text the model reads."""
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        return "You asked for fundamentals but named no ticker."
    stock = yf.Ticker(ticker)
    return "\n\n".join([
        _yahoo_block(
            "company profile",
            lambda: stock.info,
            lambda info: profile_table(ticker, info),
        ),
        _yahoo_block(
            "quarterly income statement",
            lambda: stock.quarterly_income_stmt,
            lambda frame: quarters_table(ticker, frame),
        ),
        _rating_block(ticker, client),
    ])


def _yahoo_block(what: str, call, render) -> str:
    """One yfinance read, retried on a rate limit, and rendered. A failure is
    one line, not an exception: the model is told and decides with what it
    has."""
    try:
        payload = yf_retry(call)
    except Exception:
        log.warning("Fundamentals: the %s read failed", what, exc_info=True)
        return f"The {what} could not be fetched from Yahoo Finance."
    return render(payload)


def _rating_block(ticker: str, client) -> str:
    """The analyst consensus, from Webull. The one block still on the market
    data vendor, because its route exists on the sandbox host."""
    client = client or quotes.get_api_client()
    if client is None:
        return (
            f"The analyst rating for {ticker} is not available: the market data "
            "vendor is not configured."
        )
    from webull.data.quotes.instrument import Instrument

    instrument = Instrument(client)
    return _block(
        "analyst rating",
        lambda: instrument.get_analyst_rating(ticker),
        lambda body: rating_line(ticker, body),
    )


def _block(what: str, call, render) -> str:
    """One vendor call, paced and rendered. A failure is one line, not an
    exception: the model is told and decides with what it has."""
    try:
        response = quotes.market_data_request(call, what)
    except Exception:
        log.warning("Fundamentals: the %s call failed", what, exc_info=True)
        return f"The {what} could not be fetched from the vendor."
    return render(_body(response))


def _body(response) -> dict:
    body = response.json() if hasattr(response, "json") else response
    if isinstance(body, list):
        body = body[0] if body and isinstance(body[0], dict) else {}
    return body if isinstance(body, dict) else {}


def profile_table(ticker: str, info) -> str:
    """The company's name, sector and industry, then its ratios, one a row.

    ``info`` is yfinance's dict. For an unknown symbol it is a stub such as
    ``{"trailingPegRatio": None}``, so "no usable field" reads as no data
    rather than as an empty table the model might fabricate around.
    """
    info = info if isinstance(info, dict) else {}
    rows = [(label, _figure(info.get(key))) for key, label in _RATIOS]
    rows = [(label, value) for label, value in rows if value is not None]
    name = str(info.get("longName") or info.get("shortName") or "").strip()
    if not rows and not name:
        return f"No company profile on record for {ticker}."
    head = f"{ticker} is {name}" if name else ticker
    where = ", ".join(str(part) for part in (info.get("sector"), info.get("industry")) if part)
    if where:
        head += f", in {where}"
    if not rows:
        return f"{head}. No ratios on record for it at Yahoo Finance."
    currency = info.get("financialCurrency") or info.get("currency")
    money = f", money in {currency}" if currency else ""
    lines = [
        f"{head}. Its ratios as Yahoo Finance reports them{money}; a margin, a "
        "return or a growth rate is a fraction of one:",
        "",
        "| Ratio | Value |",
        "|---|---|",
    ]
    lines += [f"| {label} | {value} |" for label, value in rows]
    return "\n".join(lines)


def quarters_table(ticker: str, frame) -> str:
    """The last quarters' revenue, profit and EPS, newest first, headed by the
    quarter's end date.

    yfinance hands a DataFrame with the statement's rows down and the period
    ends across. Only the rows in ``_QUARTER_ROWS`` are shown; a value the
    vendor left empty is a dash, so a row missing one quarter keeps its
    columns aligned.
    """
    if frame is None or getattr(frame, "empty", True):
        return f"No quarterly income statement on record for {ticker}."
    columns = sorted(frame.columns, reverse=True)[:_QUARTERS]
    rows = []
    for key, label in _QUARTER_ROWS:
        if key not in frame.index:
            continue
        row = frame.loc[key]
        if getattr(row, "ndim", 1) > 1:
            row = row.iloc[0]
        values = [_figure(row.get(column)) for column in columns]
        if all(value is None for value in values):
            continue
        rows.append((label, [value if value is not None else "—" for value in values]))
    if not rows:
        return f"No quarterly income statement on record for {ticker}."
    header = " | ".join(_period_end(column) for column in columns)
    lines = [
        f"{ticker}'s last {len(columns)} quarters from Yahoo Finance, newest first, "
        "each headed by the quarter's end date, as the vendor reports them:",
        "",
        f"| Metric | {header} |",
        "|---|" + "---|" * len(columns),
    ]
    lines += [f"| {label} | " + " | ".join(values) + " |" for label, values in rows]
    return "\n".join(lines)


def _period_end(column) -> str:
    """A column label as YYYY-MM-DD, from a Timestamp or a string."""
    text = column.strftime("%Y-%m-%d") if hasattr(column, "strftime") else str(column)
    return text[:10]


def _figure(value) -> str | None:
    """A number as the vendor sent it. ``None`` and NaN are left out. A
    number of a thousand or more is printed whole, with thousands separators.
    A smaller one keeps four significant digits, because a return on assets
    of 0.0141 must not print as 0.01."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value.strip() or None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    return f"{number:.4g}"


def rating_line(ticker: str, body: dict) -> str:
    """The analyst consensus as counts, never as a verdict of ours."""
    total = body.get("number")
    if total in (None, "", 0, "0"):
        return f"No analyst rating on record for {ticker}."
    counts = ", ".join(
        f"{label} {body.get(key, '—')}"
        for key, label in (
            ("strong_buy", "strong buy"), ("buy", "buy"), ("hold", "hold"),
            ("sell", "sell"), ("under_perform", "underperform"),
        )
    )
    since = str(body.get("effective_start_date") or "")[:10]
    tail = f" (as of {since})" if since else ""
    return f"Analysts on {ticker}: {total} in total — {counts}{tail}."
