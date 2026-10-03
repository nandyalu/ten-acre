"""Rule-based market-regime snapshot (no LLM): VIX level, SPY vs its 200-day
average, and the 10Y–3M treasury spread, each fetched from yfinance and each
optional — a failed fetch degrades the message instead of blocking it.
Classification is a simple negative-count: 0 risk-on 🟢, 1 mixed 🟡, 2+
risk-off 🔴. Blocking — call via asyncio.to_thread.
"""
import datetime
from dataclasses import dataclass

import yfinance as yf
from tradingagents.dataflows.vendors.yahoo.common import yf_retry

from backend.services.positions import drop_incomplete_bars

_VIX_RISK_LEVEL = 25.0


@dataclass
class RegimeData:
    as_of: datetime.date
    vix: float | None
    spy_price: float | None
    spy_ma200: float | None
    curve_spread_pct: float | None  # 10Y minus 3M, in percentage points

    @property
    def spy_vs_ma_pct(self) -> float | None:
        if self.spy_price is None or not self.spy_ma200:
            return None
        return (self.spy_price / self.spy_ma200 - 1) * 100


def _last_close(symbol: str) -> float | None:
    try:
        history = drop_incomplete_bars(yf_retry(lambda: yf.Ticker(symbol).history(period="5d")))
        return float(history["Close"].iloc[-1]) if len(history) else None
    except Exception:
        return None


def _normalize_yield(value: float) -> float:
    """Yahoo has quoted ^TNX/^IRX both as percent and as percent×10 over the
    years; no treasury yield is plausibly >25%, so use that to disambiguate."""
    return value / 10 if value > 25 else value


def fetch_regime() -> RegimeData:
    vix = _last_close("^VIX")
    spy_price = spy_ma200 = None
    try:
        history = drop_incomplete_bars(yf_retry(lambda: yf.Ticker("SPY").history(period="1y")))
        if len(history) >= 200:
            spy_price = float(history["Close"].iloc[-1])
            spy_ma200 = float(history["Close"].tail(200).mean())
    except Exception:
        pass
    ten_year = _last_close("^TNX")
    three_month = _last_close("^IRX")
    spread = None
    if ten_year is not None and three_month is not None:
        spread = _normalize_yield(ten_year) - _normalize_yield(three_month)
    return RegimeData(
        as_of=datetime.date.today(),
        vix=vix,
        spy_price=spy_price,
        spy_ma200=spy_ma200,
        curve_spread_pct=spread,
    )


# --- Pure classification/formatting ---------------------------------------------


def classify_regime(
    vix: float | None, spy_vs_ma_pct: float | None, curve_spread_pct: float | None
) -> tuple[str, str]:
    """(label, emoji) from however many indicators are available."""
    available = 0
    negatives = 0
    if vix is not None:
        available += 1
        negatives += vix >= _VIX_RISK_LEVEL
    if spy_vs_ma_pct is not None:
        available += 1
        negatives += spy_vs_ma_pct < 0
    if curve_spread_pct is not None:
        available += 1
        negatives += curve_spread_pct < 0
    if available == 0:
        return "Unknown", "⚪"
    if negatives == 0:
        return "Risk-on", "🟢"
    if negatives == 1:
        return "Mixed", "🟡"
    return "Risk-off", "🔴"


def _vix_word(vix: float) -> str:
    if vix < 15:
        return "calm"
    if vix < 20:
        return "normal"
    if vix < 25:
        return "elevated"
    if vix < 30:
        return "high"
    return "extreme"


def format_regime_message(data: RegimeData) -> str:
    label, emoji = classify_regime(data.vix, data.spy_vs_ma_pct, data.curve_spread_pct)
    parts = [f"{emoji} **Market regime: {label}**"]
    if data.vix is not None:
        parts.append(f"VIX {data.vix:.1f} ({_vix_word(data.vix)})")
    if data.spy_vs_ma_pct is not None:
        side = "above" if data.spy_vs_ma_pct >= 0 else "below"
        parts.append(
            f"SPY ${data.spy_price:,.2f} is {abs(data.spy_vs_ma_pct):.1f}% {side} "
            f"its 200-day avg (${data.spy_ma200:,.2f})"
        )
    if data.curve_spread_pct is not None:
        shape = "inverted" if data.curve_spread_pct < 0 else "normal"
        parts.append(f"10Y–3M spread {data.curve_spread_pct:+.2f}% ({shape})")
    if len(parts) == 1:
        parts.append("no market data available right now")
    return " · ".join(parts)
