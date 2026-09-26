"""Order execution against an Alpaca paper account.

The second broker, beside ``sandbox_broker`` (Webull). It exists so that a
person can run this experiment without a funded Webull account: Alpaca paper
keys need an email address and nothing else. ``broker`` picks one of the two
from ``BROKER``. Every public name here matches ``sandbox_broker``, and every
return value has the shape the callers already read, so no caller knows which
broker it has.

**Four guards, the same four as Webull's in Alpaca's terms.** All of them
narrow. None of them widens. An account must pass every one.

1. **The host is Alpaca's paper host, and nothing else is written in this
   module.** Paper keys and live keys are different key pairs, and a live key
   is refused by the paper host. ``_assert_paper()`` runs immediately before
   every order and checks the host again, so an edit that adds a live host
   fails at the first order.
2. **The account number carries the ``PA`` prefix.** Every Alpaca paper
   account number starts with it, and a live one does not.
3. **The account class is resolved from the account's own margin multiplier.**
   ``ALPACA_ACCOUNT_CLASS`` is ``cash`` (the default) or ``margin``. A cash
   account has a multiplier of 1. Alpaca paper accounts start as margin
   accounts with a multiplier of 4; set ``max_margin_multiplier`` to 1 in the
   account configuration to trade one as cash. The account must also have
   shorting turned off, because this app is long-only.
4. **``ALPACA_ACCOUNT_NUMBER`` names the one account this deployment owns.** An
   unset or empty value stops order flow. There is no fallback.

**Where Alpaca differs from Webull, and how the difference is hidden:**

- **A replace makes a new order.** Webull keeps the ``client_order_id`` of a
  replaced order, and the ledger relies on that. Alpaca gives the replacement a
  new id and marks the old order ``replaced``, with ``replaced_by`` pointing at
  the new one. ``_current`` follows that chain, so the ledger's id still finds
  the order that rests now.
- **Status words differ.** ``_STATUS`` maps Alpaca's to the words the callers
  test for: ``FILLED``, ``PARTIAL_FILLED``, ``CANCELLED``, ``REJECTED``,
  ``EXPIRED``, and ``SUBMITTED`` for everything still working.
- **The exits of a bracket share its time in force.** A day bracket's stop and
  target expire at the close. So the bracket is ``gtc``, and its entry is a
  marketable limit, which fills at once in a normal market.
"""
import datetime
import logging
import os
import uuid

import requests

log = logging.getLogger("ten-acre.alpaca_broker")

PAPER_HOST = "https://paper-api.alpaca.markets"
_HOST = PAPER_HOST
_TIMEOUT = 20

_PAPER_ACCOUNT_PREFIX = "PA"
_ACCOUNT_CLASSES = ("CASH", "MARGIN")
_DEFAULT_ACCOUNT_CLASS = "CASH"

# The same values as sandbox_broker's. agent_book reads them from there, and
# the Webull values are the stricter ones, so a book screened for Webull is
# never refused here for them.
ENTRY_LIMIT_BUFFER_PCT = 0.5
BUYING_POWER_MARGIN_PCT = 2.0

# Alpaca's order status, in the words the callers test for.
_STATUS = {
    "filled": "FILLED",
    "partially_filled": "PARTIAL_FILLED",
    "canceled": "CANCELLED",
    "expired": "EXPIRED",
    "rejected": "REJECTED",
    "suspended": "REJECTED",
}

_account_id: str | None = None


class NotSandboxError(RuntimeError):
    """Raised when this module is about to reach a host other than paper."""


class NoAccountConfiguredError(RuntimeError):
    """Raised when ALPACA_ACCOUNT_NUMBER names no account this deployment may use."""


def _keys() -> tuple[str, str]:
    return (
        (os.environ.get("ALPACA_API_KEY") or "").strip(),
        (os.environ.get("ALPACA_API_SECRET") or "").strip(),
    )


def is_paper() -> bool:
    """True when keys are set and this module talks to the paper host only."""
    return all(_keys()) and _HOST == PAPER_HOST


def _assert_sandbox() -> None:
    if _HOST != PAPER_HOST:
        raise NotSandboxError(f"Refusing to place an order: {_HOST} is not Alpaca's paper host.")
    if not all(_keys()):
        raise NotSandboxError("Refusing to place an order: ALPACA_API_KEY or ALPACA_API_SECRET is not set.")


def _request(method: str, path: str, **kwargs):
    key, secret = _keys()
    response = requests.request(
        method,
        f"{_HOST}{path}",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        timeout=_TIMEOUT,
        **kwargs,
    )
    if response.status_code >= 400:
        # Alpaca's own message is the useful part: "insufficient qty available
        # for order", "stop price must not be greater than base price". It
        # names no key, so it can go to the log and to the agent.
        try:
            message = response.json().get("message") or response.text
        except ValueError:
            message = response.text
        raise RuntimeError(f"Alpaca refused {method} {path.split('?')[0]}: HTTP {response.status_code}, {message}")
    return response.json() if response.content else None


def configured_account() -> str:
    """The one account this deployment may trade, from ``ALPACA_ACCOUNT_NUMBER``.

    Required, with no default: a default would let two deployments share one
    book, and the record could not say which of them placed an order.
    """
    configured = (os.environ.get("ALPACA_ACCOUNT_NUMBER") or "").strip()
    if not configured:
        raise NoAccountConfiguredError(
            "ALPACA_ACCOUNT_NUMBER is not set, so this deployment does not know which "
            "account it owns and will not place orders. Set it to the account number "
            "(PA…) of the Alpaca paper account this container should trade."
        )
    return configured


def tradeable_account_class() -> str:
    """``CASH`` or ``MARGIN``, from ``ALPACA_ACCOUNT_CLASS``. A typo fails here."""
    configured = (os.environ.get("ALPACA_ACCOUNT_CLASS") or "").strip().upper()
    if not configured:
        return _DEFAULT_ACCOUNT_CLASS
    if configured not in _ACCOUNT_CLASSES:
        raise ValueError(f"ALPACA_ACCOUNT_CLASS must be cash or margin, got {configured!r}")
    return configured


def _account_class(account: dict) -> str:
    try:
        return "CASH" if float(account.get("multiplier") or 0) == 1 else "MARGIN"
    except (TypeError, ValueError):
        return "UNKNOWN"


def get_paper_account_id(*, refresh: bool = False) -> str | None:
    """The account's id when it passes every guard, else None. Cached."""
    global _account_id
    if _account_id and not refresh:
        return _account_id
    _assert_sandbox()
    try:
        wanted_number = configured_account()
    except NoAccountConfiguredError as exc:
        log.error("%s", exc)
        return None
    account = _request("GET", "/v2/account") or {}
    number = str(account.get("account_number") or "")
    if not number.startswith(_PAPER_ACCOUNT_PREFIX):
        log.error("Account %s is not an Alpaca paper account — refusing to use it", number)
        return None
    if number != wanted_number:
        log.error(
            "These keys reach account %s, not ALPACA_ACCOUNT_NUMBER=%s — this deployment "
            "will not place orders", number, wanted_number,
        )
        return None
    wanted_class, found_class = tradeable_account_class(), _account_class(account)
    if found_class != wanted_class:
        log.error(
            "Account %s is a %s account (multiplier %s) and ALPACA_ACCOUNT_CLASS is %s — "
            "refusing to use it", number, found_class.lower(), account.get("multiplier"),
            wanted_class.lower(),
        )
        return None
    if account.get("shorting_enabled"):
        log.error("Account %s allows shorting; turn it off (no_shorting) before this app trades it", number)
        return None
    _account_id = str(account.get("id"))
    return _account_id


def _ready() -> None:
    _assert_sandbox()
    if get_paper_account_id() is None:
        raise RuntimeError("No simulated account available to trade")


def get_balance() -> dict | None:
    """The account, plus the unsettled cash in the shape ``agent`` reads.

    On a cash account Alpaca's ``buying_power`` is the settled cash, so the
    difference from ``cash`` is what has not settled. On a margin account
    unsettled cash is not a limit, so it is 0.
    """
    _assert_sandbox()
    if get_paper_account_id() is None:
        return None
    account = _request("GET", "/v2/account") or {}
    unsettled = 0.0
    if _account_class(account) == "CASH":
        try:
            unsettled = max(0.0, float(account["cash"]) - float(account["buying_power"]))
        except (KeyError, TypeError, ValueError):
            unsettled = 0.0
    return {"account_currency_assets": [{"unsettled_cash": unsettled}], "alpaca": account}


def get_positions() -> dict[str, float] | None:
    """Symbol -> quantity held, for comparison with the app's own ledger."""
    _assert_sandbox()
    if get_paper_account_id() is None:
        return None
    held: dict[str, float] = {}
    for row in _request("GET", "/v2/positions") or []:
        if str(row.get("asset_class", "us_equity")) != "us_equity":
            continue
        try:
            quantity = float(row.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if symbol and quantity:
            held[symbol] = held.get(symbol, 0.0) + quantity
    return held


def _assert_not_short(ticker: str, quantity: float) -> None:
    """Refuse a sell larger than the account holds. Same rule as Webull's."""
    held = get_positions()
    if held is None:
        log.warning("Could not read positions before selling %s — allowing, but the long-only check did not run", ticker)
        return
    have = held.get(ticker.upper().strip(), 0.0)
    if quantity > have + 1e-9:
        raise ValueError(f"refusing to sell {quantity:g} {ticker}: the account holds {have:g}.")


def orders_in(row: dict) -> list[dict]:
    """The order and the legs inside it. Alpaca nests a bracket's exits in ``legs``."""
    return [row, *[leg for leg in row.get("legs") or [] if isinstance(leg, dict)]]


def _placed(client_order_id: str, body) -> dict:
    return {
        "client_order_id": client_order_id,
        "placed_at": datetime.datetime.now(datetime.timezone.utc),
        "response": body,
    }


def _check_side(side: str, quantity: float) -> str:
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    return side


def place_market_order(ticker: str, side: str, quantity: float) -> dict:
    """A day market order. Raises on refusal, like Webull's."""
    _assert_sandbox()
    side = _check_side(side, quantity)
    if side == "SELL":
        _assert_not_short(ticker, quantity)
    _ready()
    client_order_id = uuid.uuid4().hex
    log.info("Placing paper %s %s x%d", side, ticker.upper(), int(quantity))
    body = _request("POST", "/v2/orders", json={
        "symbol": ticker.upper().strip(),
        "qty": str(int(quantity)),
        "side": side.lower(),
        "type": "market",
        "time_in_force": "day",
        "client_order_id": client_order_id,
    })
    return _placed(client_order_id, body)


def place_limit_order(
    ticker: str, side: str, quantity: float, limit_price: float, time_in_force: str = "DAY"
) -> dict:
    """A plain limit order, day or GTC, with no exits. Raises on refusal."""
    _assert_sandbox()
    side = _check_side(side, quantity)
    if limit_price <= 0:
        raise ValueError(f"limit_price must be positive, got {limit_price}")
    time_in_force = time_in_force.upper()
    if time_in_force not in ("DAY", "GTC"):
        raise ValueError(f"time_in_force must be DAY or GTC, got {time_in_force!r}")
    if side == "SELL":
        _assert_not_short(ticker, quantity)
    _ready()
    client_order_id = uuid.uuid4().hex
    log.info("Placing paper %s LIMIT %s x%d at %.2f (%s)", side, ticker.upper(), int(quantity), limit_price, time_in_force)
    body = _request("POST", "/v2/orders", json={
        "symbol": ticker.upper().strip(),
        "qty": str(int(quantity)),
        "side": side.lower(),
        "type": "limit",
        "limit_price": f"{limit_price:.2f}",
        "time_in_force": time_in_force.lower(),
        "client_order_id": client_order_id,
    })
    return _placed(client_order_id, body)


def _exits_of(body: dict, stop_price, target_price, quantity: float, placed_at) -> list[dict]:
    """One entry per exit leg the broker created, with the id it gave the leg."""
    exits = []
    for leg in body.get("legs") or []:
        kind = "stop" if leg.get("type") in ("stop", "stop_limit") else "target"
        exits.append({
            "client_order_id": leg.get("client_order_id"),
            "kind": kind,
            "price": stop_price if kind == "stop" else target_price,
            "quantity": quantity,
            "placed_at": placed_at,
        })
    return exits


def place_bracket_order(
    ticker: str,
    quantity: float,
    price: float,
    stop_price: float | None = None,
    target_price: float | None = None,
) -> dict:
    """Buy, and rest the exits under it, in one submission.

    Both exits make a ``bracket``. One exit makes an ``oto``, because Alpaca's
    bracket needs both. The legs stay held until the entry fills. The order is
    ``gtc`` so that its exits do not expire at the close (see the module
    docstring).
    """
    _assert_sandbox()
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
    if stop_price is None and target_price is None:
        raise ValueError("a bracket needs at least one exit level")
    entry_limit = round(price * (1 + ENTRY_LIMIT_BUFFER_PCT / 100), 2)
    if stop_price is not None and stop_price >= entry_limit:
        raise ValueError(f"stop {stop_price} is not below the entry limit {entry_limit}")
    if target_price is not None and target_price <= entry_limit:
        raise ValueError(f"target {target_price} is not above the entry limit {entry_limit}")
    _ready()
    client_order_id = uuid.uuid4().hex
    order = {
        "symbol": ticker.upper().strip(),
        "qty": str(int(quantity)),
        "side": "buy",
        "type": "limit",
        "limit_price": f"{entry_limit:.2f}",
        "time_in_force": "gtc",
        "order_class": "bracket" if stop_price is not None and target_price is not None else "oto",
        "client_order_id": client_order_id,
    }
    if stop_price is not None:
        order["stop_loss"] = {"stop_price": f"{stop_price:.2f}"}
    if target_price is not None:
        order["take_profit"] = {"limit_price": f"{target_price:.2f}"}
    log.info(
        "Placing paper %s for %s x%d — entry limit %.2f, stop %s, target %s",
        order["order_class"], order["symbol"], int(quantity), entry_limit, stop_price, target_price,
    )
    body = _request("POST", "/v2/orders", json=order) or {}
    placed_at = datetime.datetime.now(datetime.timezone.utc)
    return {
        "client_order_id": client_order_id,
        "entry_limit": entry_limit,
        "placed_at": placed_at,
        "response": body,
        "exits": _exits_of(body, stop_price, target_price, float(int(quantity)), placed_at),
    }


def place_exit_bracket(
    ticker: str,
    quantity: float,
    stop_price: float | None = None,
    target_price: float | None = None,
) -> list[dict]:
    """Rest a stop and a take-profit under a position the account holds.

    Both levels make one ``oco`` order, so the broker cancels one leg when the
    other fills. One level makes a plain GTC stop or limit. Returns one entry
    per leg placed.
    """
    _assert_sandbox()
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    if stop_price is not None and stop_price <= 0:
        raise ValueError(f"stop price must be positive, got {stop_price}")
    if target_price is not None and target_price <= 0:
        raise ValueError(f"target price must be positive, got {target_price}")
    if stop_price is None and target_price is None:
        return []
    _assert_not_short(ticker, quantity)
    _ready()
    base = {
        "symbol": ticker.upper().strip(),
        "qty": str(int(quantity)),
        "side": "sell",
        "time_in_force": "gtc",
        "client_order_id": uuid.uuid4().hex,
    }
    log.info("Placing paper exits for %s x%d — stop %s, target %s", base["symbol"], int(quantity), stop_price, target_price)
    placed_at = datetime.datetime.now(datetime.timezone.utc)
    if stop_price is not None and target_price is not None:
        body = _request("POST", "/v2/orders", json={
            **base,
            "type": "limit",
            "order_class": "oco",
            "take_profit": {"limit_price": f"{target_price:.2f}"},
            "stop_loss": {"stop_price": f"{stop_price:.2f}"},
        }) or {}
        # The OCO's own order is the take-profit limit; its one leg is the stop.
        return [
            {"client_order_id": base["client_order_id"], "kind": "target", "price": target_price, "placed_at": placed_at},
            *[{k: v for k, v in e.items() if k != "quantity"}
              for e in _exits_of(body, stop_price, target_price, 0, placed_at)],
        ]
    if stop_price is not None:
        _request("POST", "/v2/orders", json={**base, "type": "stop", "stop_price": f"{stop_price:.2f}"})
        return [{"client_order_id": base["client_order_id"], "kind": "stop", "price": stop_price, "placed_at": placed_at}]
    _request("POST", "/v2/orders", json={**base, "type": "limit", "limit_price": f"{target_price:.2f}"})
    return [{"client_order_id": base["client_order_id"], "kind": "target", "price": target_price, "placed_at": placed_at}]


def _by_client_id(client_order_id: str) -> dict | None:
    try:
        return _request("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id})
    except RuntimeError:
        return None


def _current(client_order_id: str) -> dict | None:
    """The order that rests now for a ledger id, after any number of replaces."""
    order = _by_client_id(client_order_id)
    for _ in range(20):  # a bound, not a count anyone expects to reach
        if not order or order.get("status") != "replaced" or not order.get("replaced_by"):
            return order
        order = _request("GET", f"/v2/orders/{order['replaced_by']}")
    return order


def replace_exit(client_order_id: str, kind: str, price: float) -> bool:
    """Move a resting stop or target. Raises when the broker refuses."""
    _assert_sandbox()
    if kind not in ("stop", "target"):
        raise ValueError(f"kind must be stop or target, got {kind!r}")
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
    _ready()
    order = _current(client_order_id)
    if not order:
        raise RuntimeError(f"Alpaca has no order {client_order_id}")
    field = "stop_price" if kind == "stop" else "limit_price"
    log.info("Moving the resting %s on order %s to %.2f", kind, client_order_id[:8], price)
    _request("PATCH", f"/v2/orders/{order['id']}", json={field: f"{price:.2f}"})
    return True


def cancel_order(client_order_id: str) -> bool:
    """Cancel a resting order. False when it could not be cancelled."""
    _assert_sandbox()
    try:
        _ready()
        order = _current(client_order_id)
        if not order:
            return False
        _request("DELETE", f"/v2/orders/{order['id']}")
        return True
    except Exception:
        log.exception("Couldn't cancel %s", client_order_id)
        return False


def get_order_detail(client_order_id: str) -> dict | None:
    """The order in the shape the callers read: status, filled_price, filled_quantity."""
    _assert_sandbox()
    if get_paper_account_id() is None:
        return None
    order = _current(client_order_id)
    if not order:
        return None
    return {
        "client_order_id": client_order_id,
        "symbol": order.get("symbol"),
        "side": str(order.get("side") or "").upper(),
        "status": _STATUS.get(str(order.get("status")), "SUBMITTED"),
        "filled_price": order.get("filled_avg_price"),
        "filled_quantity": order.get("filled_qty"),
        "alpaca": order,
    }
