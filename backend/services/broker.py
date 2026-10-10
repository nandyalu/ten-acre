"""The broker this deployment trades through: Webull's sandbox, Alpaca paper, or
the in-process simulator.

``BROKER`` picks it: ``webull`` (the default), ``alpaca`` or ``sim``. Both modules have
the same public names and return the same shapes, so a caller imports this
module and never learns which one it has. The guards live in each module and
run there, before every order; this module adds none and removes none.

The functions below are plain wrappers, not a module ``__getattr__``, so a test
can patch one of them the ordinary way.
"""
import os

from backend.services import alpaca_broker, quotes, sandbox_broker, sim_broker

_BROKERS = ("webull", "alpaca", "sim")


def name() -> str:
    """``webull``, ``alpaca`` or ``sim``. A typo in ``BROKER`` fails here, not at an order."""
    chosen = (os.environ.get("BROKER") or "webull").strip().lower()
    if chosen not in _BROKERS:
        raise ValueError(f"BROKER must be one of {_BROKERS}, got {chosen!r}")
    return chosen


def _impl():
    return {"alpaca": alpaca_broker, "sim": sim_broker}.get(name(), sandbox_broker)


def is_paper() -> bool:
    """True when orders can only reach a simulated account.

    For Webull that is ``WEBULL_SANDBOX=1``. For Alpaca it is keys that are set
    and a module that knows only the paper host. The simulator has no broker to
    reach, so it is always true.
    """
    if name() == "webull":
        return quotes.is_sandbox()
    return _impl().is_paper()


def configured_account(): return _impl().configured_account()
def tradeable_account_class(): return _impl().tradeable_account_class()
def get_paper_account_id(*, refresh=False):
    return _impl().get_paper_account_id(refresh=True) if refresh else _impl().get_paper_account_id()
def get_balance(): return _impl().get_balance()
def get_positions(): return _impl().get_positions()
def orders_in(row): return _impl().orders_in(row)
def place_market_order(ticker, side, quantity): return _impl().place_market_order(ticker, side, quantity)
def place_limit_order(ticker, side, quantity, limit_price, time_in_force="DAY"):
    return _impl().place_limit_order(ticker, side, quantity, limit_price, time_in_force)
def place_bracket_order(ticker, quantity, price, stop_price=None, target_price=None):
    return _impl().place_bracket_order(ticker, quantity, price, stop_price, target_price)
def place_exit_bracket(ticker, quantity, stop_price=None, target_price=None):
    return _impl().place_exit_bracket(ticker, quantity, stop_price, target_price)
def replace_exit(client_order_id, kind, price): return _impl().replace_exit(client_order_id, kind, price)
def cancel_order(client_order_id): return _impl().cancel_order(client_order_id)
def get_order_detail(client_order_id): return _impl().get_order_detail(client_order_id)


def no_account_errors() -> tuple[type[Exception], ...]:
    """Every module's "no account named" error, for a caller that catches it."""
    return (
        sandbox_broker.NoAccountConfiguredError,
        alpaca_broker.NoAccountConfiguredError,
        sim_broker.NoAccountConfiguredError,
    )
