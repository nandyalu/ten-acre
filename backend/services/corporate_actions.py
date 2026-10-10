"""Splits and spin-offs, applied once, the way a broker applies them.

**Why.** A split changes the unit of every number about a stock: its price
history, a holder's share count and cost, the resting stop and target, the
levels an analysis stated. Left alone, NVDA's 10-for-1 reads as a 90% crash,
and a resting stop at the old price fills at the open. The bar cache cannot
fix it alone, because its sources disagree: Webull's daily history is raw,
Yahoo's is already adjusted (checked 2026-10-02 on CTVA, $103.95 against
$77.65 for the same close).

**What applying one does**, to every row of the ticker from before ``ex_date``:

- the cached daily bars are dropped, so they refetch; a raw Webull row is
  adjusted on the way in (``adjust_raw``), a Yahoo row arrives adjusted;
- the stored minute bars are adjusted in place, since both their sources are raw;
- the agent's ledger: a split multiplies shares by the ratio and divides
  prices by it, so every cost and every profit is unchanged; a spin-off moves
  part of the cost of the shares still held to the new stock;
- the simulated broker's orders the same way, resting stops and targets
  included, and a spin-off's new shares appear as a filled buy;
- the levels and prices of stored signals, so the grading compares like with like;
- the cached last price.

The agent is told in "What was noticed since your last pass", with what it
holds now. A spin-off's price factor is the parent's share of the combined
value at the close of the ex-date, so it is applied after that close; until
then the parent's simulated stop and target are paused, and the agent is told.

The source is Alpaca's corporate-actions feed (splits, reverse splits,
spin-offs). Without Alpaca keys, Yahoo's split list stands in, and spin-offs
are not seen.
"""
import datetime
import json
import logging
from collections import defaultdict

from sqlmodel import Session, select

from backend.database.models import (
    AgentTrade, CorporateAction, DailyBar, IntradayBar, Signal, SimOrder, TickerPrice, WatchlistTicker,
)

log = logging.getLogger("ten-acre.corporate_actions")

# How far back a check looks. It covers the 200-day average the stock cells
# read, so a ticker tracked for the first time gets its old splits applied.
LOOKBACK = datetime.timedelta(days=400)


def _engine():
    from backend.database.engine import engine

    return engine


def _ex_open(ex_date: datetime.date) -> datetime.datetime:
    """9:30 ET on the ex-date, naive UTC. A row from before it is in old units."""
    from backend.services import watchdog

    local = datetime.datetime.combine(ex_date, watchdog._MARKET_OPEN, watchdog.US_MARKET_TZ)
    return local.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _today() -> datetime.date:
    from backend.services import market_clock

    return market_clock.now_et().date()


# --- finding them ------------------------------------------------------------


def _from_alpaca(rows: list[dict]) -> list[CorporateAction]:
    out = []
    for row in rows:
        kind = row.get("type")
        ex = datetime.date.fromisoformat(str(row["ex_date"])[:10])
        if kind in ("forward_splits", "reverse_splits"):
            out.append(CorporateAction(
                id=str(row["id"]), ticker=row["symbol"], kind="split", ex_date=ex,
                ratio=float(row["new_rate"]) / float(row["old_rate"]),
            ))
        elif kind == "spin_offs":
            out.append(CorporateAction(
                id=str(row["id"]), ticker=row["source_symbol"], kind="spin_off", ex_date=ex,
                ratio=float(row["new_rate"]) / float(row["source_rate"]), child=row["new_symbol"],
            ))
    return out


def _from_yfinance(tickers, start) -> list[CorporateAction]:
    import yfinance as yf

    out = []
    for ticker in tickers:
        try:
            splits = yf.Ticker(ticker).splits
        except Exception:
            log.warning("Yahoo's split list failed for %s", ticker, exc_info=True)
            continue
        for when, ratio in splits.items():
            ex = when.date()
            if ex >= start and ratio and float(ratio) != 1.0:
                out.append(CorporateAction(
                    id=f"yf-{ticker}-{ex}", ticker=ticker, kind="split", ex_date=ex, ratio=float(ratio),
                ))
    return out


def fetch(tickers, start: datetime.date, end: datetime.date) -> list[CorporateAction]:
    from backend.services import alpaca_broker

    from backend.services import market_feed

    tickers = sorted({t.upper() for t in tickers if t})
    if not tickers:
        return []
    answer = market_feed.ask(
        "corporate_actions", tickers=",".join(tickers), start=start.isoformat(), end=end.isoformat()
    )
    if answer is not market_feed.MISSING:
        return [
            CorporateAction(**{**row, "ex_date": datetime.date.fromisoformat(row["ex_date"])})
            for row in answer or []
        ]
    if alpaca_broker.is_paper():
        return _from_alpaca(alpaca_broker.corporate_actions(tickers, start, end))
    return _from_yfinance(tickers, start)


def tickers_to_check() -> set[str]:
    """Everything this deployment holds, rests an order on, or tracks."""
    with Session(_engine()) as s:
        tickers = {row.ticker for row in s.exec(select(WatchlistTicker)).all()}
        tickers |= {t.ticker for t in s.exec(select(AgentTrade)).all()}
        tickers |= {o.ticker for o in s.exec(select(SimOrder).where(SimOrder.status.in_(("HELD", "SUBMITTED")))).all()}
    return tickers


def check(tickers=None, today: datetime.date | None = None) -> list[str]:
    """Find, record and apply every action due. Returns what was told."""
    today = today or _today()
    tickers = set(tickers) if tickers is not None else tickers_to_check()
    found = fetch(tickers, today - LOOKBACK, today)
    with Session(_engine()) as s:
        for action in found:
            if s.get(CorporateAction, action.id) is None:
                s.add(action)
        s.commit()
        due = s.exec(
            select(CorporateAction)
            .where(CorporateAction.applied_at.is_(None), CorporateAction.ex_date <= today)
            .order_by(CorporateAction.ex_date)
        ).all()
        due_ids = [a.id for a in due]
    told = []
    for action_id in due_ids:
        message = apply(action_id, today)
        if message:
            told.append(message)
    return told


# --- applying them -----------------------------------------------------------


def adjust_raw(ticker: str, rows: list[dict]) -> list[dict]:
    """Daily rows from a raw source, in the units of every applied action."""
    with Session(_engine()) as s:
        actions = s.exec(
            select(CorporateAction).where(
                CorporateAction.ticker == ticker.upper(), CorporateAction.applied_at.is_not(None)
            )
        ).all()
    for action in actions:
        share = action.ratio if action.kind == "split" else 1.0
        for row in rows:
            if row["date"] < action.ex_date:
                for key in ("open", "high", "low", "close"):
                    row[key] *= action.price_factor
                row["volume"] *= share
    return rows


def _spin_factor(action: CorporateAction) -> float | None:
    """The parent's share of the combined value at the ex-date's close, or
    None while that session is not complete."""
    from backend.services import bars

    parent = [b for b in bars.get_bars(action.ticker, action.ex_date, action.ex_date) if b.date == action.ex_date.isoformat()]
    child = [b for b in bars.get_bars(action.child, action.ex_date, action.ex_date) if b.date == action.ex_date.isoformat()]
    if not parent or not child:
        return None
    return parent[0].close / (parent[0].close + action.ratio * child[0].close)


def apply(action_id: str, today: datetime.date) -> str | None:
    """Apply one action. None when a spin-off waits for its ex-date's close."""
    from backend.database import db
    from backend.services import bars

    with Session(_engine()) as s:
        action = s.get(CorporateAction, action_id)
        if action is None or action.applied_at is not None:
            return None
        if action.kind == "spin_off":
            factor = _spin_factor(action)
            if factor is None:
                return _pause(s, action)
        else:
            factor = 1.0 / action.ratio
        share = action.ratio if action.kind == "split" else 1.0
        cutoff = _ex_open(action.ex_date)
        ticker = action.ticker

        for row in s.exec(select(DailyBar).where(DailyBar.ticker == ticker)).all():
            s.delete(row)
        for row in s.exec(select(IntradayBar).where(IntradayBar.ticker == ticker, IntradayBar.timestamp < cutoff)).all():
            row.open, row.high, row.low, row.close = (v * factor for v in (row.open, row.high, row.low, row.close))
            row.volume *= share
        for sig in s.exec(select(Signal).where(Signal.ticker == ticker, Signal.signal_date < action.ex_date)).all():
            for field in ("entry_price", "stop_loss", "price_target", "price_at_signal"):
                if getattr(sig, field) is not None:
                    setattr(sig, field, getattr(sig, field) * factor)
            if sig.price_at_evaluation is not None and sig.evaluation_date < action.ex_date:
                sig.price_at_evaluation *= factor
        price = s.get(TickerPrice, ticker)
        if price is not None and price.fetched_at.replace(tzinfo=None) < cutoff:
            price.price *= factor

        if action.kind == "split":
            held = _split_ledger(s, ticker, cutoff, factor, share)
            told = _split_message(action, held)
        else:
            held, child_qty = _spin_ledger(s, action, cutoff, factor)
            told = _spin_message(action, factor, held, child_qty)

        action.price_factor = factor
        action.applied_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        s.commit()

    bars._earliest_attempt.pop(ticker, None)
    bars._last_fetch.pop(ticker, None)
    db.record_alert(ticker, "corporate_action", f"corporate-action-{action_id}", told)
    log.info("Applied %s: %s", action_id, told)
    return told


def _split_ledger(s: Session, ticker: str, cutoff, factor: float, share: float) -> float:
    """Shares times the ratio, prices divided by it, on every row before the
    ex-date. Returns the shares held now."""
    for t in s.exec(select(AgentTrade).where(AgentTrade.ticker == ticker)).all():
        if (t.filled_at or t.placed_at).replace(tzinfo=None) < cutoff:
            t.quantity *= share
            t.price = t.price * factor if t.price is not None else None
            t.limit_price = t.limit_price * factor if t.limit_price is not None else None
    for o in s.exec(select(SimOrder).where(SimOrder.ticker == ticker)).all():
        if o.placed_at < cutoff:
            o.quantity *= share
            o.filled_quantity = o.filled_quantity * share if o.filled_quantity is not None else None
            o.filled_price = o.filled_price * factor if o.filled_price is not None else None
            o.limit_price = o.limit_price * factor if o.limit_price is not None else None
            o.stop_price = o.stop_price * factor if o.stop_price is not None else None
            if o.status in ("HELD", "SUBMITTED"):
                # The bars before the open are in the old units; never read them.
                o.checked_through = max(o.checked_through, cutoff - datetime.timedelta(minutes=1))
    s.flush()
    return _held(s, ticker)


def _held(s: Session, ticker: str) -> float:
    total = 0.0
    for t in s.exec(select(AgentTrade).where(AgentTrade.ticker == ticker, AgentTrade.status == "filled")).all():
        total += t.quantity if t.side == "buy" else -t.quantity
    return max(total, 0.0)


def _open_lots(s: Session, ticker: str, cutoff) -> list[tuple[AgentTrade, float]]:
    """(buy row, shares of it still held at the cutoff), first in first out."""
    lots: list[list] = []
    rows = s.exec(
        select(AgentTrade).where(AgentTrade.ticker == ticker, AgentTrade.status == "filled")
        .order_by(AgentTrade.filled_at, AgentTrade.id)
    ).all()
    for t in rows:
        if (t.filled_at or t.placed_at).replace(tzinfo=None) >= cutoff:
            continue
        if t.side == "buy":
            lots.append([t, t.quantity])
            continue
        left = t.quantity
        while left > 1e-9 and lots:
            used = min(left, lots[0][1])
            lots[0][1] -= used
            left -= used
            if lots[0][1] <= 1e-9:
                lots.pop(0)
    return [(row, remaining) for row, remaining in lots if remaining > 1e-9]


def _spin_ledger(s: Session, action: CorporateAction, cutoff, factor: float) -> tuple[float, int]:
    """Move the spun-off part of the cost of the shares still held to the new
    stock. A lot sold in part before the ex-date is split in two, so what was
    realized before stays as it was. Returns (parent held, child shares)."""
    ticker, moved, held = action.ticker, 0.0, 0.0
    for row, remaining in _open_lots(s, ticker, cutoff):
        held += remaining
        original = row.price
        moved += remaining * original * (1 - factor)
        if remaining < row.quantity - 1e-9:
            s.add(AgentTrade(**{
                **row.model_dump(exclude={"id"}),
                "client_order_id": f"{row.client_order_id}-spin-{action.id[:8]}",
                "quantity": remaining, "price": original * factor,
            }))
            row.quantity -= remaining
        else:
            row.price = original * factor
    for t in s.exec(select(AgentTrade).where(AgentTrade.ticker == ticker, AgentTrade.status == "pending")).all():
        t.limit_price = t.limit_price * factor if t.limit_price is not None else None
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    paused = set(json.loads(action.note or "[]"))
    for o in s.exec(select(SimOrder).where(SimOrder.ticker == ticker)).all():
        if o.status in ("HELD", "SUBMITTED") and o.placed_at < cutoff:
            o.limit_price = o.limit_price * factor if o.limit_price is not None else None
            o.stop_price = o.stop_price * factor if o.stop_price is not None else None
            if o.client_order_id in paused:
                o.status, o.checked_through = "SUBMITTED", now
    child_qty = int(held * action.ratio)
    if child_qty > 0:
        cost = round(moved / child_qty, 4)
        s.add(AgentTrade(
            ticker=action.child, side="buy", quantity=child_qty, price=cost, status="filled",
            placed_at=cutoff, filled_at=cutoff, client_order_id=f"spin-{action.id}",
            reason=f"spun off from {ticker}: {action.ratio:g} {action.child} for each {ticker} share",
        ))
        if s.exec(select(SimOrder).where(SimOrder.ticker == ticker)).first() is not None:
            s.add(SimOrder(
                client_order_id=f"spin-{action.id}", ticker=action.child, side="BUY", order_type="market",
                quantity=child_qty, status="FILLED", placed_at=cutoff, checked_through=cutoff,
                filled_at=cutoff, filled_price=cost, filled_quantity=child_qty,
            ))
        if s.get(WatchlistTicker, action.child) is None:
            s.add(WatchlistTicker(ticker=action.child))
    return held, child_qty


def _pause(s: Session, action: CorporateAction) -> str | None:
    """Hold the parent's simulated stop and target until the factor is known."""
    from backend.database import db

    if action.note:
        return None  # paused already
    cutoff = _ex_open(action.ex_date)
    ids = []
    for o in s.exec(select(SimOrder).where(SimOrder.ticker == action.ticker, SimOrder.status == "SUBMITTED")).all():
        if o.side == "SELL" and o.placed_at < cutoff:
            o.status = "HELD"
            ids.append(o.client_order_id)
    action.note = json.dumps(ids)
    s.commit()
    if not ids:
        return None
    told = (
        f"{action.ticker} spins off {action.child} on {action.ex_date}: {action.ratio:g} {action.child} "
        f"for each {action.ticker} share. The spin-off moves {action.ticker}'s price by an amount not "
        "known until that session closes, so its resting stop and target are paused until then. "
        "They come back after the close, scaled to the new price. Nothing protects the position "
        "in between unless you act."
    )
    db.record_alert(action.ticker, "corporate_action", f"corporate-action-pause-{action.id}", told)
    return told


def _split_message(action: CorporateAction, held: float) -> str:
    name = (
        f"a {action.ratio:g}-for-1 split" if action.ratio >= 1
        else f"a 1-for-{1 / action.ratio:g} reverse split"
    )
    told = (
        f"{action.ticker} had {name} on {action.ex_date}. Every {action.ticker} price from before "
        f"that day is now in the new units, and every share count with it."
    )
    if held:
        told += f" You hold {held:g} shares; your cost, your profit and your resting stop and target moved with them."
    return told


def _spin_message(action: CorporateAction, factor: float, held: float, child_qty: int) -> str:
    told = (
        f"{action.ticker} spun off {action.child} on {action.ex_date}: {action.ratio:g} {action.child} for "
        f"each {action.ticker} share. {action.ticker}'s earlier prices are scaled by {factor:.3f}, its "
        "share of the combined value at that day's close."
    )
    if child_qty:
        told += (
            f" You now hold {child_qty} {action.child}, and it is on your watchlist. Part of your "
            f"{action.ticker} cost moved to it, so the two together cost what {held:g} {action.ticker} "
            f"did. Your {action.ticker} stop and target were scaled the same way; {action.child} has none."
        )
    return told
