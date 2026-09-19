"""Key figures about a company, for the agent's ``fundamentals`` fetch.

Webull has no filter screener: its screens are rank lists (most active,
gainers, sectors). So "screen by PE or PEG", which the agent asked for on
2026-09-17, is two steps: fetch the rank lists (``candidates``), then ask for
each name's figures. This is the second step. Three vendor calls, each paced
by ``quotes.market_data_request``: the last reports' ratios (financial
indicators), the rank among up to twenty industry peers on one metric
(industry comparison), and the analyst consensus counts (analyst rating).

**Rendered as tables, and nothing is invented.** The payloads are the
documented ones (developer.webull.com/apis/docs/reference/financial-indicators.md,
industry-comparison.md and get-analyst-rating.md) and arrive as untyped dicts.
A block that fails or comes back empty says so in one line. A value is
printed as the vendor sent it, with no unit added, because the docs state
none.

Blocking (HTTP). The agent's pass already runs on a worker thread.
"""
import logging

from backend.services import quotes

log = logging.getLogger("ten-acre.fundamentals")

# The documented metric keys, with the name the model reads. Order is the
# order of the table.
_INDICATORS = [
    ("roe", "Return on equity"),
    ("roa", "Return on assets"),
    ("net_margin", "Net margin"),
    ("debt_to_assets", "Debt to assets"),
    ("diluted_eps_incl_extra", "EPS, diluted"),
    ("naps", "Book value per share"),
    ("ocf_ps", "Operating cash flow per share"),
]
_PERIODS = {0: "FY", 1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}
# Reports shown, newest first. Four quarters is a year of trend; more is a
# wider table the model would skim.
_REPORTS = 4
# Peers shown from the industry table, plus the ticker's own row when it
# ranks below them.
_PEERS_SHOWN = 6
_COMPARE_BY = "PE_TTM"


def describe(ticker: str, client=None) -> str:
    """The three blocks for one ticker, as text the model reads."""
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        return "You asked for fundamentals but named no ticker."
    client = client or quotes.get_api_client()
    if client is None:
        return f"Fundamentals for {ticker} are not available: the market data vendor is not configured."
    from webull.data.quotes.fundamentals import Fundamentals
    from webull.data.quotes.instrument import Instrument

    fundamentals, instrument = Fundamentals(client), Instrument(client)
    return "\n\n".join([
        _block(
            "financial indicators",
            lambda: fundamentals.get_financials_indicators(ticker, type="QUARTERLY", count=_REPORTS),
            lambda body: indicators_table(ticker, body),
        ),
        _block(
            "industry comparison",
            lambda: fundamentals.get_industry_comparison(ticker, sort_by=_COMPARE_BY),
            lambda body: industry_table(ticker, body),
        ),
        _block(
            "analyst rating",
            lambda: instrument.get_analyst_rating(ticker),
            lambda body: rating_line(ticker, body),
        ),
    ])


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


def indicators_table(ticker: str, body: dict) -> str:
    """Metrics down, reports across, newest first.

    ``values`` maps a metric to a list of ``{fiscal_year, fiscal_period,
    value}``. The periods a company has reported are the columns, taken from
    whichever metrics carry them, so a metric missing one report shows a dash
    there rather than shifting its row.
    """
    values = body.get("values") if isinstance(body.get("values"), dict) else {}
    periods: set[tuple[int, int]] = set()
    cells: dict[str, dict[tuple[int, int], str]] = {}
    for key, _ in _INDICATORS:
        for point in values.get(key) or []:
            if not isinstance(point, dict):
                continue
            try:
                when = (int(point.get("fiscal_year")), int(point.get("fiscal_period")))
            except (TypeError, ValueError):
                continue
            if point.get("value") is None:
                continue
            periods.add(when)
            cells.setdefault(key, {})[when] = str(point["value"])
    if not cells:
        return f"No financial indicators on record for {ticker}."
    columns = sorted(periods, reverse=True)[:_REPORTS]
    header = " | ".join(f"{year} {_PERIODS.get(period, period)}" for year, period in columns)
    lines = [
        f"{ticker}'s financial indicators, newest report first, in {body.get('currency') or 'the vendor’s units'}, as the vendor reports them:",
        "",
        f"| Metric | {header} |",
        "|---|" + "---|" * len(columns),
    ]
    for key, label in _INDICATORS:
        row = cells.get(key)
        if not row:
            continue
        lines.append(f"| {label} | " + " | ".join(row.get(when, "—") for when in columns) + " |")
    return "\n".join(lines)


def industry_table(ticker: str, body: dict) -> str:
    """Where the ticker ranks among its industry peers on one metric."""
    rows = [r for r in (body.get("data") or []) if isinstance(r, dict) and r.get("symbol")]
    if not rows:
        return f"No industry comparison on record for {ticker}."

    def rank_of(row) -> int:
        try:
            return int(row.get("rank"))
        except (TypeError, ValueError):
            return 10**6

    rows.sort(key=rank_of)
    metric = str(body.get("type") or _COMPARE_BY)
    industry = str(body.get("industry_name") or "its industry")
    own = next((r for r in rows if str(r["symbol"]).upper() == ticker), None)
    shown = rows[:_PEERS_SHOWN]
    if own is not None and own not in shown:
        shown.append(own)
    head = (
        f"{ticker} ranks {rank_of(own)} of {len(rows)} in {industry} by {metric}"
        if own is not None
        else f"{ticker} is not in the vendor's {industry} table ({len(rows)} names, by {metric})"
    )
    lines = [f"{head}:", "", f"| Rank | Ticker | Company | {metric} |", "|---|---|---|---|"]
    for row in shown:
        marker = " (this one)" if row is own else ""
        lines.append(
            f"| {row.get('rank', '—')} | {str(row['symbol']).upper()}{marker} | "
            f"{str(row.get('name') or '')[:40]} | {row.get('value', '—')} |"
        )
    return "\n".join(lines)


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
