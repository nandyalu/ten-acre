"""What one analysis cost, in dollars.

The telemetry has stored tokens and seconds since it was added, for one
decision: keep self-hosting or move to a vendor. Tokens do not answer that on
their own — somebody has to price them — and doing that by hand went wrong
twice in a week, both times by using a blended rate.

**Input and output are priced separately here, always.** List is $0.30 per 1M
input against $2.50 per 1M output on Flash-Lite, an 8x gap, so a single blended
rate is a property of the workload's output ratio rather than of the model. A
rate derived from a Flash-Lite sweep at 8.5% completion and applied to gemma4's
history at 16% understated it by 39%.

A self-hosted model has no vendor bill, and reporting $0.00 for it would be the
wrong kind of true — it reads as free when it is really paid for in GPU time.
So a local run is priced from the energy it actually used, and ``basis`` says
which of the two a figure is. The point is to put both on one axis: an analysis
that costs 5.6 cents at a vendor and 0.3 cents in electricity is the whole
self-host-versus-cloud argument in two numbers.

Prices are list, current as of 2026-08-25, and are **estimates**. Two billed
readings so far came in at 78% and 103% of list, which is why nothing here
claims to be the invoice. See CLAUDE.md for both measurements.
"""
from dataclasses import dataclass

# Vendor list prices, dollars per 1M tokens: (input, output).
# Verified against ai.google.dev/gemini-api/docs/pricing on 2026-08-25.
VENDOR_PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.30, 2.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
}

# What one busy card draws. The estimate charges wall-clock seconds at one
# card's draw. Since 2026-09-13 the four analysts run at the same time on up to
# four cards, so the estimate is low for that stage of about three minutes.
GPU_WATTS = 100.0
ELECTRICITY_USD_PER_KWH = 0.22


@dataclass
class Cost:
    usd: float
    basis: str  # "vendor" | "electricity"

    @property
    def label(self) -> str:
        return "list price" if self.basis == "vendor" else "GPU electricity"


def is_vendor(model: str | None) -> bool:
    return bool(model) and model in VENDOR_PRICES


def estimate(
    model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    duration_seconds: float | None,
) -> Cost | None:
    """What this run cost, or None when there is nothing to price.

    None rather than zero for an unmeasured run, for the same reason the
    telemetry stores NULL: a zero would read as a free analysis rather than an
    unrecorded one.
    """
    if is_vendor(model):
        if prompt_tokens is None and completion_tokens is None:
            return None
        price_in, price_out = VENDOR_PRICES[model]
        usd = (prompt_tokens or 0) / 1e6 * price_in + (completion_tokens or 0) / 1e6 * price_out
        return Cost(usd=usd, basis="vendor")

    # Self-hosted: no invoice, but not free either.
    if not duration_seconds:
        return None
    kwh = duration_seconds / 3600 * GPU_WATTS / 1000
    return Cost(usd=kwh * ELECTRICITY_USD_PER_KWH, basis="electricity")


def total(rows) -> dict[str, float]:
    """Summed cost per basis across many signals, for an aggregate view.

    Kept split rather than added together: a dollar of vendor billing and a
    dollar of electricity are not the same dollar. One arrives as an invoice
    and the other is already being spent to keep a home server running.
    """
    out = {"vendor": 0.0, "electricity": 0.0}
    for row in rows:
        cost = estimate(
            row.model, row.prompt_tokens, row.completion_tokens, row.duration_seconds
        )
        if cost is not None:
            out[cost.basis] += cost.usd
    return out
