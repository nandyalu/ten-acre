"""Order execution against Webull's simulated (sandbox) account.

This is the only module in the app that places orders, and it refuses to do
anything unless ``quotes.is_sandbox()`` is true. That check is not a
convenience — it is the guarantee. Production credentials and sandbox
credentials are different key pairs pointing at different hosts
(api.webull.com vs api.sandbox.webull.com), so with the sandbox flag off there
is no simulated account to reach and every order would land on real money.

Three further belts, because one flag is one typo away from being wrong:

- The target account is resolved by ``account_class == INDIVIDUAL_CASH``, never
  hardcoded, and the resolver rejects any account whose number doesn't look
  like a simulated one (``DE…``). Every sandbox account is DE-prefixed, in both
  the DEM and DEL series; a production account carries no such prefix, which is
  what makes the check worth having.
- Every order goes through ``_assert_sandbox()`` immediately before the call,
  not merely at module import, so flipping the environment mid-process cannot
  leave a live client armed.
- ``WEBULL_ACCOUNT_ID`` names the one account this deployment owns, and an
  unset value stops order flow rather than falling back to anything. Added
  2026-09-09 for a second container: the checks above narrow five accounts to
  one, and would narrow to the *same* one for every deployment applying the
  same rule, leaving a record unable to say which container traded. See
  ``configured_account``.

All three narrow; none of them widens. An account must pass every one.

Field names come from the Place Order reference (developer.webull.com,
common-order-place), not from the SDK — the SDK passes dicts straight through
and validates nothing.
"""
import datetime
import logging
import os
import uuid

from backend.services import quotes

log = logging.getLogger("ten-acre.sandbox_broker")

# Which simulated account this deployment trades. The sandbox exposes five —
# Crypto, Futures, Events Cash, Individual Cash and Individual Margin — and
# only the last two are equities accounts this app could ever use.
#
# It is configurable because a second deployment runs the autonomous-analyst
# experiment against the *other* equities account, so the two never share a
# book. It is configurable from a fixed set rather than from free text: a typo
# in an env var must not be able to point order flow at the crypto or futures
# account.
_EQUITY_ACCOUNT_CLASSES = ("INDIVIDUAL_CASH", "INDIVIDUAL_MARGIN")
_DEFAULT_ACCOUNT_CLASS = "INDIVIDUAL_CASH"

# Every simulated account number on the sandbox host is DE-prefixed. Treated as
# a required marker rather than a curiosity: if an account without it appears
# while the sandbox flag is on, something is wrong enough to stop rather than
# trade.
#
# **It was "DEM" until 2026-09-03, and that was an assumption from half the
# data.** The host issues both DEM and DEL, across classes — FUTURES DEM272Y8,
# EVENTS_CASH DEL84669, INDIVIDUAL_CASH DEL546C9, INDIVIDUAL_MARGIN DEM67245,
# CRYPTO DEL744J6. DEM was simply what the two equity accounts happened to use
# when this was written. A Webull paper reset handed the cash account a DEL
# number, the check refused it, and the agent had no account to trade on the
# first morning of the experiment.
#
# This is the weaker of the two guards and always was. The one that matters is
# `_assert_sandbox()`: without WEBULL_SANDBOX=1 the app never reaches a trading
# endpoint at all.
_SIMULATED_ACCOUNT_PREFIX = "DE"

_account_id: str | None = None


class NoAccountConfiguredError(RuntimeError):
    """Raised when WEBULL_ACCOUNT_ID names no account this deployment may use."""


def configured_account() -> str:
    """The one account this deployment is allowed to trade, from
    ``WEBULL_ACCOUNT_ID``.

    **Required, with no default, and that is the point.** Added 2026-09-09,
    when a second container was about to run: the three guards above narrow
    the sandbox's five accounts to "an ``INDIVIDUAL_CASH`` account numbered
    ``DE…``", which is one account — and would be the *same* one account for
    every container that applies the same rule. Two deployments sharing a book
    leaves a record that cannot say which of them placed an order.

    A default would defeat it. The value of this guard is that a person wrote
    down which book this container owns, and a guard that guesses when nobody
    wrote anything down is not a guard. So an unset variable stops order flow
    rather than falling back to the old behaviour.

    Read per call rather than at import, like ``tradeable_account_class``, so a
    test can change it without reimporting the module.
    """
    configured = (os.environ.get("WEBULL_ACCOUNT_ID") or "").strip()
    if not configured:
        raise NoAccountConfiguredError(
            "WEBULL_ACCOUNT_ID is not set, so this deployment does not know which "
            "account it owns and will not place orders. Set it to the account "
            "number (DE…) or the account id of the simulated account this "
            "container should trade."
        )
    return configured


def _is_the_configured_account(account: dict, wanted_id: str) -> bool:
    """Whether this is the account the operator named.

    Matches the account number or the internal id, whichever was pasted. The
    two are different shapes — ``DEL546C9`` against a numeric id — so accepting
    either cannot make a wrong value look right: a value matching neither
    matches nothing and the app refuses. Requiring the *right* one of two
    identifiers, with nothing in the console saying which the app wanted, would
    only produce confusing refusals.
    """
    return wanted_id in (
        str(account.get("account_number", "")).strip(),
        str(account.get("account_id", "")).strip(),
    )


def tradeable_account_class() -> str:
    """The account class this deployment trades.

    Read per call rather than at import so a test can change it, and validated
    against the known equities classes so a mistyped env var fails loudly here
    instead of quietly selecting nothing — or, worse, something.
    """
    configured = (os.environ.get("WEBULL_ACCOUNT_CLASS") or "").strip().upper()
    if not configured:
        return _DEFAULT_ACCOUNT_CLASS
    if configured not in _EQUITY_ACCOUNT_CLASSES:
        raise ValueError(
            f"WEBULL_ACCOUNT_CLASS must be one of {_EQUITY_ACCOUNT_CLASSES}, got {configured!r}"
        )
    return configured


class NotSandboxError(RuntimeError):
    """Raised instead of placing an order when the client isn't simulated."""


def orders_in(row: dict) -> list[dict]:
    """The individual orders inside one order-history row.

    Webull answers with *combo* wrappers, not orders: each row carries
    ``client_order_id`` / ``combo_type`` / ``combo_order_id`` and an ``orders``
    list holding the real order objects. A single-leg order is still wrapped in
    a one-element list, so there is no flat case to special-case. The docs hint
    at this only obliquely ("if they are group orders, will be returned
    together"), and reading the top level instead finds no symbol, no side and
    no quantity — every row parses to nothing.
    """
    orders = row.get("orders")
    if isinstance(orders, list):
        return [order for order in orders if isinstance(order, dict)]
    return [row] if "symbol" in row else []


def _assert_sandbox() -> None:
    if not quotes.is_sandbox():
        raise NotSandboxError(
            "Refusing to place an order: WEBULL_SANDBOX is not set, so these "
            "credentials point at the real brokerage account."
        )


def _rows(response) -> list[dict]:
    """The list body of a Webull response, however it arrives."""
    body = response.json() if hasattr(response, "json") else response
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        for key in ("data", "result", "list", "items"):
            value = body.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def get_paper_account_id(*, refresh: bool = False) -> str | None:
    """The simulated individual-cash account, or None when it can't be
    resolved. Cached — the id is stable for the life of the credentials."""
    global _account_id
    if _account_id and not refresh:
        return _account_id
    _assert_sandbox()
    client = quotes.get_api_client()
    if client is None:
        return None
    from webull.trade.trade.v2.account_info_v2 import AccountV2

    wanted = tradeable_account_class()
    try:
        wanted_id = configured_account()
    except NoAccountConfiguredError as exc:
        # Not a crash: every caller already treats None as "no account to
        # trade" and stops. Logged at error because a deployment in this state
        # is switched on and doing nothing, which should be loud.
        log.error("%s", exc)
        return None

    for account in _rows(AccountV2(client).get_account_list()):
        if str(account.get("account_class", "")).upper() != wanted:
            continue
        number = str(account.get("account_number", ""))
        if not number.startswith(_SIMULATED_ACCOUNT_PREFIX):
            log.error(
                "Account %s is class %s but its number is not simulated — refusing to use it",
                number,
                wanted,
            )
            return None
        # Applied last, so it can only narrow what the checks above already
        # allowed. An account has to be simulated, of the right class, and
        # then the one this deployment was told it owns.
        #
        # `continue`, not `return`: the host can hold several accounts of one
        # class, and finding the wrong one first must not abort the search for
        # the right one. Returning here meant a deployment naming the second
        # such account resolved nothing — caught by
        # test_two_deployments_naming_different_accounts_resolve_differently.
        if not _is_the_configured_account(account, wanted_id):
            log.debug("Skipping %s: not the account this deployment owns", number)
            continue
        _account_id = str(account.get("account_id"))
        return _account_id
    log.error(
        "No %s account matching WEBULL_ACCOUNT_ID=%s found on the sandbox host — "
        "this deployment will not place orders",
        wanted,
        wanted_id,
    )
    return None


def get_balance() -> dict | None:
    """Raw balance payload for the paper account. Used to reconcile, never to
    size an order — the simulated account is funded with $1,000,000 and the
    agent's budget is far smaller (see backend/services/agent_book.py)."""
    _assert_sandbox()
    client = quotes.get_api_client()
    account_id = get_paper_account_id()
    if client is None or account_id is None:
        return None
    from webull.trade.trade.v2.account_info_v2 import AccountV2

    response = AccountV2(client).get_account_balance(account_id)
    body = response.json() if hasattr(response, "json") else response
    return body if isinstance(body, dict) else None


def get_positions() -> dict[str, float] | None:
    """Symbol -> quantity actually held in the simulated account.

    The app keeps its own ledger and does not read positions to decide
    anything; this exists so the two can be compared. A disagreement means the
    ledger is wrong, which is worth knowing loudly.
    """
    _assert_sandbox()
    client = quotes.get_api_client()
    account_id = get_paper_account_id()
    if client is None or account_id is None:
        return None
    from webull.trade.trade.v2.account_info_v2 import AccountV2

    held: dict[str, float] = {}
    for row in _rows(AccountV2(client).get_account_position(account_id)):
        if str(row.get("instrument_type", "EQUITY")).upper() not in ("EQUITY", ""):
            continue
        symbol = str(row.get("symbol", "")).strip().upper()
        try:
            quantity = float(row.get("quantity", 0))
        except (TypeError, ValueError):
            continue
        if symbol and quantity:
            held[symbol] = held.get(symbol, 0.0) + quantity
    return held


def _assert_not_short(ticker: str, quantity: float) -> None:
    """Refuse a sell larger than the account actually holds.

    This app is long-only, and until now the *broker* enforced that for free:
    a cash account rejects an oversized sell outright with
    GENERATE_NEW_SHORT_POSITION. A margin account does not — it accepts the
    order and opens a short.

    So the moment a deployment points at the margin account, the backstop that
    has been catching this vanishes, and the rule has to live here instead.
    ``agent_book.validate`` already refuses the agent's own oversized sells;
    this catches the other way in, which is a bug in this app's own code —
    a stale quantity in a reset, a ledger that has drifted from the account.

    Unknown positions are not treated as zero. If the position read fails we
    cannot prove the sell is covered, but we also cannot prove it is naked,
    and refusing every exit because a quote call failed would leave real
    positions unprotected. It logs and allows.
    """
    held = get_positions()
    if held is None:
        log.warning(
            "Could not read positions before selling %s — allowing, but the "
            "long-only check did not run",
            ticker,
        )
        return
    have = held.get(ticker.upper().strip(), 0.0)
    if quantity > have + 1e-9:
        raise ValueError(
            f"refusing to sell {quantity:g} {ticker}: the account holds {have:g}. "
            "This app is long-only, and a margin account will happily short."
        )


def place_market_order(ticker: str, side: str, quantity: float) -> dict:
    """Place a day market order on the simulated account.

    Returns the broker's response with ``client_order_id`` echoed back, so the
    caller can tie its ledger row to the order even if the response shape
    changes. Raises rather than returning None on refusal — a silently skipped
    order would leave the ledger claiming a position that doesn't exist.
    """
    _assert_sandbox()
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    if side == "SELL":
        _assert_not_short(ticker, quantity)

    client = quotes.get_api_client()
    account_id = get_paper_account_id()
    if client is None or account_id is None:
        raise RuntimeError("No simulated account available to trade")

    from webull.trade.trade.v3.order_opration_v3 import OrderOperationV3

    # Whole shares only. The simulated venue accepts fractional quantities on
    # some symbols and rejects them on others, and a partially-supported order
    # type is not worth the reconciliation ambiguity.
    quantity_str = str(int(quantity))
    client_order_id = uuid.uuid4().hex
    order = {
        "client_order_id": client_order_id,
        "combo_type": "NORMAL",
        "instrument_type": "EQUITY",
        "entrust_type": "QTY",
        "symbol": ticker.upper().strip(),
        "market": "US",
        "side": side,
        "order_type": "MARKET",
        "time_in_force": "DAY",
        "quantity": quantity_str,
        "support_trading_session": "CORE",
    }
    log.info("Placing simulated %s %s x%s", side, order["symbol"], quantity_str)
    response = OrderOperationV3(client).place_order(account_id, [order])
    body = response.json() if hasattr(response, "json") else response
    return {
        "client_order_id": client_order_id,
        "placed_at": datetime.datetime.now(datetime.timezone.utc),
        "response": body,
    }


def place_limit_order(
    ticker: str, side: str, quantity: float, limit_price: float, time_in_force: str = "DAY"
) -> dict:
    """Place a plain limit order — an entry the agent chose a price for,
    rather than the marketable-limit ``place_bracket_order`` sends on its own.

    Not bracketed: a limit order may sit unfilled for a session or, under
    ``GTC``, for days, and the broker will not hold a combo's exit legs
    inactive that long against a master that has not filled. Exits for a
    limit buy are armed separately, once the fill is confirmed — see
    ``agent._execute_orders``.

    Raises on refusal, like ``place_market_order`` — a silently skipped order
    would leave the ledger claiming a position that does not exist.
    """
    _assert_sandbox()
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    if limit_price <= 0:
        raise ValueError(f"limit_price must be positive, got {limit_price}")
    time_in_force = time_in_force.upper()
    if time_in_force not in ("DAY", "GTC"):
        raise ValueError(f"time_in_force must be DAY or GTC, got {time_in_force!r}")
    if side == "SELL":
        _assert_not_short(ticker, quantity)

    client = quotes.get_api_client()
    account_id = get_paper_account_id()
    if client is None or account_id is None:
        raise RuntimeError("No simulated account available to trade")

    from webull.trade.trade.v3.order_opration_v3 import OrderOperationV3

    quantity_str = str(int(quantity))
    client_order_id = uuid.uuid4().hex
    order = {
        "client_order_id": client_order_id,
        "combo_type": "NORMAL",
        "instrument_type": "EQUITY",
        "entrust_type": "QTY",
        "symbol": ticker.upper().strip(),
        "market": "US",
        "side": side,
        "order_type": "LIMIT",
        "limit_price": f"{limit_price:.2f}",
        "time_in_force": time_in_force,
        "quantity": quantity_str,
        "support_trading_session": "CORE",
    }
    log.info(
        "Placing simulated %s LIMIT %s x%s at %.2f (%s)",
        side, order["symbol"], quantity_str, limit_price, time_in_force,
    )
    response = OrderOperationV3(client).place_order(account_id, [order])
    body = response.json() if hasattr(response, "json") else response
    return {
        "client_order_id": client_order_id,
        "placed_at": datetime.datetime.now(datetime.timezone.utc),
        "response": body,
    }


# How far above the market to set a bracket's entry limit. A MASTER leg cannot
# be a MARKET order (the broker answers INVALID_PARAMETER) and cannot be GTC
# either, so the entry is a marketable limit: priced through the offer, it
# behaves like a market order while capping what a fast tape can charge. A
# market order's slippage is unbounded, which on an app-enforced budget is the
# worse of the two.
ENTRY_LIMIT_BUFFER_PCT = 0.5

# The sandbox account refuses a buy unless its buying power is this much
# above the order's own cost — a separate broker rule from the slippage
# buffer above, and additive to it. Learned from a live refusal on
# 2026-09-04 (OPENAPI_DAY_BUYING_POWER_INSUFFICIENT_M_NEW), not from the
# docs. Shared with agent_book.validate(), the check that must refuse an
# order before it ever reaches the broker; see the 2026-09-17 JOURNEY.md
# entry for the refusal loop this closed.
BUYING_POWER_MARGIN_PCT = 2.0


def place_bracket_order(
    ticker: str,
    quantity: float,
    price: float,
    stop_price: float | None = None,
    target_price: float | None = None,
) -> dict:
    """Buy, and rest the exits under it, in one submission.

    The broker holds the exits inactive until the entry fills and activates
    them itself, so there is no window in which the shares are owned and
    nothing is protecting them. Arming afterwards always had that window: on
    2026-08-13 both buys filled and neither got its exits, because the sell
    legs were validated while the account still held nothing and read as a new
    short.

    Three shapes the broker rejects, all found by testing against the sandbox
    and none of them documented:

    - a ``MASTER`` leg with ``order_type`` MARKET, or with ``time_in_force``
      GTC — hence the marketable limit above;
    - a stop at or above the entry limit (``TRADE_STOP_LOSS_PRICE_LT_OPENPRICE``),
      which is the broker's own version of the check the caller already makes;
    - **any combo at all when the cash is unsettled**
      (``CANT_USE_UNSETTLE_FUNDS_FOR_COMBO_ORDER``). A plain market order may
      be placed against unsettled proceeds; a combo may not. Selling to fund a
      buy in the same pass is something the agent is explicitly told it can do,
      so this is a routine refusal rather than an edge case, and the caller
      must have a path that still works when it happens.

    ``preview_order`` accepts every one of those, so it cannot be used to check
    a combo before placing it.

    Raises on refusal, like ``place_market_order`` — a silently skipped order
    would leave the ledger claiming a position that does not exist.
    """
    _assert_sandbox()
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
    if stop_price is None and target_price is None:
        raise ValueError("a bracket needs at least one exit level")

    entry_limit = round(price * (1 + ENTRY_LIMIT_BUFFER_PCT / 100), 2)
    # Both exits are read against the entry the broker will actually accept,
    # not the last trade — a stop that is under the market but over the limit
    # takes the whole combo down with it, buy included.
    if stop_price is not None and stop_price >= entry_limit:
        raise ValueError(f"stop {stop_price} is not below the entry limit {entry_limit}")
    if target_price is not None and target_price <= entry_limit:
        raise ValueError(f"target {target_price} is not above the entry limit {entry_limit}")

    client = quotes.get_api_client()
    account_id = get_paper_account_id()
    if client is None or account_id is None:
        raise RuntimeError("No simulated account available to trade")

    from webull.trade.trade.v3.order_opration_v3 import OrderOperationV3

    base = {
        "instrument_type": "EQUITY",
        "entrust_type": "QTY",
        "symbol": ticker.upper().strip(),
        "market": "US",
        "quantity": str(int(quantity)),
        "support_trading_session": "CORE",
    }
    entry = {
        **base,
        "client_order_id": uuid.uuid4().hex,
        "combo_type": "MASTER",
        "side": "BUY",
        "order_type": "LIMIT",
        "limit_price": f"{entry_limit:.2f}",
        "time_in_force": "DAY",
    }
    legs = [entry]
    if stop_price is not None:
        legs.append({
            **base,
            "client_order_id": uuid.uuid4().hex,
            "combo_type": "STOP_LOSS",
            "side": "SELL",
            "order_type": "STOP_LOSS",
            "stop_price": f"{stop_price:.2f}",
            # GTC: an exit that expired at tonight's close would protect the
            # position for an afternoon and then quietly stop existing.
            "time_in_force": "GTC",
        })
    if target_price is not None:
        legs.append({
            **base,
            "client_order_id": uuid.uuid4().hex,
            "combo_type": "STOP_PROFIT",
            "side": "SELL",
            "order_type": "LIMIT",
            "limit_price": f"{target_price:.2f}",
            "time_in_force": "GTC",
        })

    log.info(
        "Placing simulated bracket for %s x%s — entry limit %.2f, stop %s, target %s",
        base["symbol"], base["quantity"], entry_limit, stop_price, target_price,
    )
    response = OrderOperationV3(client).place_order(account_id, legs, uuid.uuid4().hex)
    body = response.json() if hasattr(response, "json") else response
    placed_at = datetime.datetime.now(datetime.timezone.utc)
    return {
        "client_order_id": entry["client_order_id"],
        "entry_limit": entry_limit,
        "placed_at": placed_at,
        "response": body,
        "exits": [
            {
                "client_order_id": leg["client_order_id"],
                "kind": "stop" if leg["order_type"] == "STOP_LOSS" else "target",
                "price": stop_price if leg["order_type"] == "STOP_LOSS" else target_price,
                "quantity": float(leg["quantity"]),
                "placed_at": placed_at,
            }
            for leg in legs[1:]
        ],
    }


def place_exit_bracket(
    ticker: str,
    quantity: float,
    stop_price: float | None = None,
    target_price: float | None = None,
) -> list[dict]:
    """Rest the exits for a position: a stop below and a take-profit above.

    Placed as one OCO combo when both levels exist, so the broker cancels the
    other leg the moment one fills. Two independent orders would leave a window
    where both are live — a gap through the target could fill the limit and
    leave a stop still trying to sell shares that are gone.

    The accepted pairing is combo_type STOP_LOSS + STOP_PROFIT, found by
    testing: OCO+OCO and MASTER+OCO are both rejected, though the docs list
    every one of those as a valid combo_type.

    Either level may be missing — the analysis states them only when it has a
    view — and whichever exists is placed on its own. Returns one entry per leg
    actually placed.
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
    # These rest against shares that already exist, so the same rule applies.
    # A bracket's exit legs are exempt: they are placed with the entry that
    # creates the position, and the broker ties the three together.
    _assert_not_short(ticker, quantity)

    client = quotes.get_api_client()
    account_id = get_paper_account_id()
    if client is None or account_id is None:
        raise RuntimeError("No simulated account available to trade")

    from webull.trade.trade.v3.order_opration_v3 import OrderOperationV3

    bracket = stop_price is not None and target_price is not None
    base = {
        "instrument_type": "EQUITY",
        "entrust_type": "QTY",
        "symbol": ticker.upper().strip(),
        "market": "US",
        "side": "SELL",
        # GTC: an exit that expired at tonight's close would protect the
        # position for an afternoon and then quietly stop existing.
        "time_in_force": "GTC",
        "quantity": str(int(quantity)),
        "support_trading_session": "CORE",
    }

    legs: list[dict] = []
    if stop_price is not None:
        legs.append({
            **base,
            "client_order_id": uuid.uuid4().hex,
            "combo_type": "STOP_LOSS" if bracket else "NORMAL",
            "order_type": "STOP_LOSS",
            "stop_price": f"{stop_price:.2f}",
        })
    if target_price is not None:
        legs.append({
            **base,
            "client_order_id": uuid.uuid4().hex,
            "combo_type": "STOP_PROFIT" if bracket else "NORMAL",
            "order_type": "LIMIT",
            "limit_price": f"{target_price:.2f}",
        })

    combo_id = uuid.uuid4().hex if bracket else None
    log.info(
        "Placing simulated exits for %s x%s — stop %s, target %s",
        base["symbol"], quantity, stop_price, target_price,
    )
    if bracket:
        OrderOperationV3(client).place_order(account_id, legs, combo_id)
    else:
        OrderOperationV3(client).place_order(account_id, legs)

    placed_at = datetime.datetime.now(datetime.timezone.utc)
    return [
        {
            "client_order_id": leg["client_order_id"],
            "kind": "stop" if leg["order_type"] == "STOP_LOSS" else "target",
            "price": stop_price if leg["order_type"] == "STOP_LOSS" else target_price,
            "placed_at": placed_at,
        }
        for leg in legs
    ]


def replace_exit(client_order_id: str, kind: str, price: float) -> bool:
    """Move a resting exit to a new level.

    A replace rather than a cancel-and-place, which matters: cancelling first
    leaves a window with nothing resting under the position, and that window is
    exactly the state this app spends most of its effort avoiding.

    The order keeps its ``client_order_id`` — the replace identifies the order
    by it rather than issuing a new one — so the ledger row that already points
    at it stays correct and only its price changes.

    Only the price is sent. The docs are explicit that a STOP_LOSS accepts
    changes to ``stop_price`` and a LIMIT to ``limit_price``, and sending the
    fields that are not being changed is how a replace turns into a different
    order by accident.
    """
    _assert_sandbox()
    if kind not in ("stop", "target"):
        raise ValueError(f"kind must be stop or target, got {kind!r}")
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")

    client = quotes.get_api_client()
    account_id = get_paper_account_id()
    if client is None or account_id is None:
        raise RuntimeError("No simulated account available to trade")

    from webull.trade.trade.v3.order_opration_v3 import OrderOperationV3

    field = "stop_price" if kind == "stop" else "limit_price"
    modify = {"client_order_id": client_order_id, field: f"{price:.2f}"}
    log.info("Moving the resting %s on order %s to %.2f", kind, client_order_id[:8], price)
    OrderOperationV3(client).replace_order(account_id, [modify])
    return True


def cancel_order(client_order_id: str) -> bool:
    """Cancel a resting order. Used when a position is closed by other means —
    a stop left behind would try to sell shares that are no longer held."""
    _assert_sandbox()
    client = quotes.get_api_client()
    account_id = get_paper_account_id()
    if client is None or account_id is None:
        return False
    from webull.trade.trade.v3.order_opration_v3 import OrderOperationV3

    try:
        OrderOperationV3(client).cancel_order(account_id, client_order_id)
        return True
    except Exception:
        log.exception("Couldn't cancel %s", client_order_id)
        return False


def get_order_detail(client_order_id: str) -> dict | None:
    """The order itself, so the fill price can be recorded — placed at market,
    the price is only knowable after the fact.

    The endpoint answers with a *combo wrapper*, not an order: the status,
    price, and quantity all live inside a one-element ``orders`` list, and the
    top level carries none of them. Reading the top level finds a dict that
    looks plausible and parses to nothing, so every fill stays pending forever.
    ``orders_in`` unwraps it.
    """
    _assert_sandbox()
    client = quotes.get_api_client()
    account_id = get_paper_account_id()
    if client is None or account_id is None:
        return None
    from webull.trade.trade.v3.order_opration_v3 import OrderOperationV3

    response = OrderOperationV3(client).get_order_detail(account_id, client_order_id)
    body = response.json() if hasattr(response, "json") else response
    if not isinstance(body, dict):
        return None
    orders = orders_in(body)
    return orders[0] if orders else None
