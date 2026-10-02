"""The in-process simulated broker, ``BROKER=sim``. Experiment 2 trades here.

No order leaves the process. There is no broker SDK, no order host and no
credential in this module, so no edit to a setting can point it at real money.
A test checks the imports. That is the fifth guard, beside the four that
Webull and Alpaca keep in their own modules.

Every public name matches ``sandbox_broker`` and ``alpaca_broker``, and every
return value has the shape the callers already read. The agent's ledger
(``AgentTrade``) stays the book of record. It learns of a fill the way it does
from a real broker: by asking ``get_order_detail``.

**How an order fills.**

- A market order, and the entry of a bracket, fills when it is placed, at the
  price the agent was shown (``positions.get_current_price``) plus
  ``SIM_SLIPPAGE_BPS``. A closed market refuses both, as Webull does.
- A limit order, a resting stop and a resting target fill from the 1-minute
  bars after the order existed, oldest first. A buy limit fills when a bar's
  low reaches it, a target when a bar's high reaches it, at the limit or at the
  bar's open when the bar opened past it. A stop fills when a bar's low reaches
  it, at the stop or at the open below it, less the slippage. A gap down
  therefore costs what it costs at a real exchange.
- A fill is decided when an order is asked about. ``settle_pending`` asks about
  every pending order on the 15-minute poll, so a resting stop settles there.
  The bars may be some minutes old (Alpaca's free feed is 16 minutes behind);
  an order is read only up to the newest bar seen, never past it.
- One fill in a one-cancels-other group cancels the rest of the group, and an
  entry's fill puts its held exit legs to work.
- A day order expires at the close of the session it was placed for.

**What it does not model, on purpose.** Cash does not wait to settle, so a
buy can always carry its stop and target in one order: Webull's unsettled-cash
refusal is a property of that broker, not of trading. Orders fill whole, never
in part. A split is not handled here; PLAN.md item 9 must land first.
"""
import datetime
import logging
import os
import threading
import uuid

from sqlmodel import Session, select

from backend.database.models import SimOrder

log = logging.getLogger("ten-acre.sim_broker")

ACCOUNT = "SIM"

# The same values as sandbox_broker's, for the reason alpaca_broker gives:
# agent_book reads them from there, so a book screened for Webull is never
# refused here for them.
ENTRY_LIMIT_BUFFER_PCT = 0.5
BUYING_POWER_MARGIN_PCT = 2.0

_DEFAULT_SLIPPAGE_BPS = 5.0
_WORKING = ("HELD", "SUBMITTED")
# How far back one evaluation pages through minute bars. A bound on the work
# after a long outage, not a count anyone expects to reach.
_MAX_PAGES = 20
_MINUTE = datetime.timedelta(minutes=1)
_EXPIRY_GRACE = datetime.timedelta(hours=1)

_lock = threading.RLock()


def _engine():
    from backend.database.engine import engine

    return engine


class NotSandboxError(RuntimeError):
    """Kept for the shape the callers expect. This module cannot reach a broker."""


class NoAccountConfiguredError(RuntimeError):
    """Kept for the shape the callers expect. The simulated account always exists."""


def _now() -> datetime.datetime:
    """UTC, naive, the convention of every stored time in this app."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def slippage() -> float:
    """``SIM_SLIPPAGE_BPS`` as a fraction. A typo fails at the order, loudly."""
    raw = (os.environ.get("SIM_SLIPPAGE_BPS") or "").strip()
    bps = float(raw) if raw else _DEFAULT_SLIPPAGE_BPS
    if bps < 0:
        raise ValueError(f"SIM_SLIPPAGE_BPS must not be negative, got {bps}")
    return bps / 10_000


def is_paper() -> bool:
    return True


def configured_account() -> str:
    return ACCOUNT


def tradeable_account_class() -> str:
    return "CASH"


def get_paper_account_id(*, refresh: bool = False) -> str:
    return ACCOUNT


def get_balance() -> dict:
    """Nothing is ever unsettled here. See the module docstring."""
    return {"account_currency_assets": [{"unsettled_cash": 0.0}]}


def orders_in(row: dict) -> list[dict]:
    return [row]


def get_positions() -> dict[str, float]:
    """Symbol -> shares held: every filled buy less every filled sell."""
    with Session(_engine()) as session:
        rows = session.exec(select(SimOrder).where(SimOrder.status == "FILLED")).all()
    held: dict[str, float] = {}
    for row in rows:
        sign = 1 if row.side == "BUY" else -1
        held[row.ticker] = held.get(row.ticker, 0.0) + sign * (row.filled_quantity or 0)
    return {ticker: qty for ticker, qty in held.items() if qty > 1e-9}


def _market_open(at: datetime.datetime | None = None) -> bool:
    from backend.services import watchdog

    when = (at or _now()).replace(tzinfo=datetime.timezone.utc)
    return watchdog.is_us_market_hours(when)


def _quote(ticker: str) -> float:
    from backend.services import positions

    price = positions.get_current_price(ticker)
    if not price or price <= 0:
        raise RuntimeError(f"the simulated broker has no current price for {ticker}, so it cannot fill an order")
    return float(price)


def _check_side(side: str, quantity: float) -> str:
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    if quantity != int(quantity):
        raise ValueError(f"whole shares only, got {quantity}")
    return side


def _assert_not_short(ticker: str, quantity: float) -> None:
    """Refuse a sell larger than the account holds. Long only, as everywhere."""
    have = get_positions().get(ticker, 0.0)
    if quantity > have + 1e-9:
        raise ValueError(f"refusing to sell {quantity:g} {ticker}: the account holds {have:g}.")


def _refuse_when_closed(what: str) -> None:
    if not _market_open():
        raise RuntimeError(
            f"the market is closed, so the simulated broker will not take a {what} now. "
            "A limit order, a stop or a target may rest while it is closed."
        )


def _new(session: Session, **fields) -> SimOrder:
    now = _now()
    order = SimOrder(
        client_order_id=uuid.uuid4().hex, placed_at=now, checked_through=now, **fields
    )
    session.add(order)
    return order


def _fill(order: SimOrder, price: float, at: datetime.datetime) -> None:
    order.status = "FILLED"
    order.filled_price = round(price, 4)
    order.filled_quantity = order.quantity
    order.filled_at = at


def place_market_order(ticker: str, side: str, quantity: float) -> dict:
    """A market order, filled at once at the quote plus slippage."""
    ticker = ticker.upper().strip()
    side = _check_side(side, quantity)
    with _lock:
        if side == "SELL":
            _assert_not_short(ticker, quantity)
        _refuse_when_closed("market order")
        quote = _quote(ticker)
        price = quote * (1 + slippage()) if side == "BUY" else quote * (1 - slippage())
        with Session(_engine()) as session:
            order = _new(session, ticker=ticker, side=side, order_type="market", quantity=quantity)
            _fill(order, price, order.placed_at)
            session.commit()
            log.info("Simulated %s %s x%d filled at %.4f", side, ticker, int(quantity), price)
            return _placed(order)


def place_limit_order(
    ticker: str, side: str, quantity: float, limit_price: float, time_in_force: str = "DAY"
) -> dict:
    """A plain limit order. It rests at any hour and fills only in a session."""
    ticker = ticker.upper().strip()
    side = _check_side(side, quantity)
    if limit_price <= 0:
        raise ValueError(f"limit_price must be positive, got {limit_price}")
    time_in_force = time_in_force.upper()
    if time_in_force not in ("DAY", "GTC"):
        raise ValueError(f"time_in_force must be DAY or GTC, got {time_in_force!r}")
    with _lock:
        if side == "SELL":
            _assert_not_short(ticker, quantity)
        with Session(_engine()) as session:
            order = _new(
                session, ticker=ticker, side=side, order_type="limit", quantity=quantity,
                limit_price=round(limit_price, 2), time_in_force=time_in_force,
            )
            # A marketable limit fills now, at the quote or better.
            if _market_open():
                quote = _quote(ticker)
                if side == "BUY" and quote <= order.limit_price:
                    _fill(order, quote, order.placed_at)
                elif side == "SELL" and quote >= order.limit_price:
                    _fill(order, quote, order.placed_at)
            session.commit()
            return _placed(order)


def place_bracket_order(
    ticker: str,
    quantity: float,
    price: float,
    stop_price: float | None = None,
    target_price: float | None = None,
) -> dict:
    """Buy, and rest the exits under it, in one submission.

    The entry is a marketable limit at ``ENTRY_LIMIT_BUFFER_PCT`` through the
    price, as at the real brokers. Filled at once, its exits go to work at
    once. Not filled, it rests for the day and its exits wait on it.
    """
    ticker = ticker.upper().strip()
    _check_side("BUY", quantity)
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
    if stop_price is None and target_price is None:
        raise ValueError("a bracket needs at least one exit level")
    entry_limit = round(price * (1 + ENTRY_LIMIT_BUFFER_PCT / 100), 2)
    if stop_price is not None and stop_price >= entry_limit:
        raise ValueError(f"stop {stop_price} is not below the entry limit {entry_limit}")
    if target_price is not None and target_price <= entry_limit:
        raise ValueError(f"target {target_price} is not above the entry limit {entry_limit}")
    with _lock:
        _refuse_when_closed("bracket order")
        fill_at = _quote(ticker) * (1 + slippage())
        with Session(_engine()) as session:
            entry = _new(
                session, ticker=ticker, side="BUY", order_type="limit", quantity=quantity,
                limit_price=entry_limit, time_in_force="DAY",
            )
            if fill_at <= entry_limit:
                _fill(entry, fill_at, entry.placed_at)
            legs = _exit_legs(
                session, ticker, quantity, stop_price, target_price,
                parent_id=entry.client_order_id,
                status="SUBMITTED" if entry.status == "FILLED" else "HELD",
            )
            session.commit()
            placed_at = entry.placed_at.replace(tzinfo=datetime.timezone.utc)
            return {
                "client_order_id": entry.client_order_id,
                "entry_limit": entry_limit,
                "placed_at": placed_at,
                "response": {"status": entry.status},
                "exits": [
                    {
                        "client_order_id": leg.client_order_id,
                        "kind": "stop" if leg.order_type == "stop" else "target",
                        "price": leg.stop_price if leg.order_type == "stop" else leg.limit_price,
                        "quantity": float(int(quantity)),
                        "placed_at": placed_at,
                    }
                    for leg in legs
                ],
            }


def _exit_legs(
    session: Session,
    ticker: str,
    quantity: float,
    stop_price: float | None,
    target_price: float | None,
    *,
    parent_id: str | None = None,
    status: str = "SUBMITTED",
) -> list[SimOrder]:
    group_id = uuid.uuid4().hex if stop_price is not None and target_price is not None else None
    legs = []
    if stop_price is not None:
        legs.append(_new(
            session, ticker=ticker, side="SELL", order_type="stop", quantity=quantity,
            stop_price=round(stop_price, 2), time_in_force="GTC", status=status,
            parent_id=parent_id, group_id=group_id,
        ))
    if target_price is not None:
        legs.append(_new(
            session, ticker=ticker, side="SELL", order_type="limit", quantity=quantity,
            limit_price=round(target_price, 2), time_in_force="GTC", status=status,
            parent_id=parent_id, group_id=group_id,
        ))
    return legs


def place_exit_bracket(
    ticker: str,
    quantity: float,
    stop_price: float | None = None,
    target_price: float | None = None,
) -> list[dict]:
    """Rest a stop and a take-profit under shares the account holds.

    Both levels make a one-cancels-other pair. It works at any hour, because
    nothing trades until a price reaches it.
    """
    ticker = ticker.upper().strip()
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    if stop_price is not None and stop_price <= 0:
        raise ValueError(f"stop price must be positive, got {stop_price}")
    if target_price is not None and target_price <= 0:
        raise ValueError(f"target price must be positive, got {target_price}")
    if stop_price is None and target_price is None:
        return []
    with _lock:
        _assert_not_short(ticker, quantity)
        with Session(_engine()) as session:
            legs = _exit_legs(session, ticker, quantity, stop_price, target_price)
            session.commit()
            return [
                {
                    "client_order_id": leg.client_order_id,
                    "kind": "stop" if leg.order_type == "stop" else "target",
                    "price": leg.stop_price if leg.order_type == "stop" else leg.limit_price,
                    "placed_at": leg.placed_at.replace(tzinfo=datetime.timezone.utc),
                }
                for leg in legs
            ]


def _placed(order: SimOrder) -> dict:
    return {
        "client_order_id": order.client_order_id,
        "placed_at": order.placed_at.replace(tzinfo=datetime.timezone.utc),
        "response": {"status": order.status},
    }


def _get(session: Session, client_order_id: str) -> SimOrder | None:
    return session.exec(
        select(SimOrder).where(SimOrder.client_order_id == client_order_id)
    ).first()


def replace_exit(client_order_id: str, kind: str, price: float) -> bool:
    """Move a resting stop or target. Raises when there is nothing to move."""
    if kind not in ("stop", "target"):
        raise ValueError(f"kind must be stop or target, got {kind!r}")
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
    with _lock, Session(_engine()) as session:
        order = _get(session, client_order_id)
        if order is None:
            raise RuntimeError(f"the simulated broker has no order {client_order_id}")
        if order.status not in _WORKING:
            raise RuntimeError(f"order {client_order_id} is {order.status}, so it cannot be moved")
        if kind == "stop":
            order.stop_price = round(price, 2)
        else:
            order.limit_price = round(price, 2)
        session.commit()
        return True


def cancel_order(client_order_id: str) -> bool:
    """Cancel a working order and any legs that wait on it. False when it is done."""
    with _lock, Session(_engine()) as session:
        order = _get(session, client_order_id)
        if order is None:
            return False
        _advance(session, order)
        if order.status not in _WORKING:
            session.commit()
            return False
        order.status = "CANCELLED"
        for leg in session.exec(select(SimOrder).where(SimOrder.parent_id == client_order_id)).all():
            if leg.status in _WORKING:
                leg.status = "CANCELLED"
        session.commit()
        return True


def get_order_detail(client_order_id: str) -> dict | None:
    """The order in the shape the callers read, after reading the prices since
    it was last asked about."""
    with _lock, Session(_engine()) as session:
        order = _get(session, client_order_id)
        if order is None:
            return None
        _advance(session, order)
        session.commit()
        return {
            "client_order_id": order.client_order_id,
            "order_id": f"sim-{order.id}",
            "symbol": order.ticker,
            "side": order.side,
            "status": order.status,
            "filled_price": order.filled_price,
            "filled_quantity": order.filled_quantity,
        }


def _session_close_after(when: datetime.datetime) -> datetime.datetime:
    """The close, in naive UTC, of the first session that ends after ``when``."""
    from backend.services import market_calendar, watchdog

    tz = watchdog.US_MARKET_TZ
    local = when.replace(tzinfo=datetime.timezone.utc).astimezone(tz)
    day = local.date()
    for _ in range(10):
        if market_calendar.is_trading_day(day):
            end = (
                market_calendar._EARLY_CLOSE
                if day in market_calendar.early_closes(day.year)
                else watchdog._MARKET_CLOSE
            )
            close = datetime.datetime.combine(day, end, tz)
            if close > local:
                return close.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        day += datetime.timedelta(days=1)
    raise RuntimeError(f"no session close found after {when}")


def _bars_since(ticker: str, since: datetime.datetime) -> list[dict] | None:
    """The regular-session minute bars that start at or after ``since``,
    oldest first. None when no bar source is configured."""
    from backend.services import intraday

    pages: list[list[dict]] = []
    end = None
    for _ in range(_MAX_PAGES):
        page = intraday.fetch_bars(ticker, end_time=end)
        if page is None:
            return None if not pages else _flatten(pages, since)
        if not page:
            break
        pages.append(page)
        if page[0]["timestamp"] <= since:
            break
        end = page[0]["timestamp"] - _MINUTE
    return _flatten(pages, since)


def _flatten(pages: list[list[dict]], since: datetime.datetime) -> list[dict]:
    seen: dict[datetime.datetime, dict] = {}
    for page in pages:
        for bar in page:
            if bar["timestamp"] >= since and _market_open(bar["timestamp"]):
                seen[bar["timestamp"]] = bar
    return [seen[t] for t in sorted(seen)]


def _fills_on(order: SimOrder, bar: dict) -> float | None:
    """The price ``order`` fills at inside ``bar``, or None."""
    if order.order_type == "stop":
        if bar["low"] <= order.stop_price:
            return min(order.stop_price, bar["open"]) * (1 - slippage())
        return None
    if order.side == "BUY":
        if bar["low"] <= order.limit_price:
            return min(order.limit_price, bar["open"])
        return None
    if bar["high"] >= order.limit_price:
        return max(order.limit_price, bar["open"])
    return None


def _advance(session: Session, order: SimOrder) -> None:
    """Read the prices since ``order`` was last read, and fill or expire it."""
    if order.status != "SUBMITTED":
        return
    # A bar is read only once its minute has begun after the order existed:
    # nothing earlier in that minute could have filled it.
    since = order.checked_through.replace(second=0, microsecond=0) + _MINUTE
    expires = _session_close_after(order.placed_at) if order.time_in_force == "DAY" else None
    bars = _bars_since(order.ticker, since)
    if bars is None:
        # No bar source at all: read the quote as one point, in a session only.
        bars = []
        if _market_open():
            now = _now()
            price = _quote(order.ticker)
            bars = [{"timestamp": now, "open": price, "high": price, "low": price, "close": price}]
    for bar in bars:
        if expires is not None and bar["timestamp"] >= expires:
            break
        price = _fills_on(order, bar)
        if price is not None:
            _fill(order, price, bar["timestamp"])
            _after_fill(session, order, bar["timestamp"])
            return
        order.checked_through = bar["timestamp"]
    # Expire only once the bars up to the close are read, or an hour after it
    # when they never come. A delayed feed reaches the last minutes late.
    read_to_close = order.checked_through >= expires - _MINUTE if expires else False
    if expires is not None and (read_to_close or _now() >= expires + _EXPIRY_GRACE):
        order.status = "EXPIRED"
        for leg in session.exec(select(SimOrder).where(SimOrder.parent_id == order.client_order_id)).all():
            if leg.status in _WORKING:
                leg.status = "CANCELLED"


def _after_fill(session: Session, order: SimOrder, at: datetime.datetime) -> None:
    if order.group_id:
        for other in session.exec(select(SimOrder).where(SimOrder.group_id == order.group_id)).all():
            if other.client_order_id != order.client_order_id and other.status in _WORKING:
                other.status = "CANCELLED"
    for leg in session.exec(select(SimOrder).where(SimOrder.parent_id == order.client_order_id)).all():
        if leg.status == "HELD":
            leg.status = "SUBMITTED"
            # The entry's own minute is spent: an exit is read from the next bar.
            leg.checked_through = at
