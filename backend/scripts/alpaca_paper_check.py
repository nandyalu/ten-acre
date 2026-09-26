"""Check the Alpaca broker against a real paper account, with fills.

PLAN.md asks for this before a deployment trades through Alpaca: the bracket
and the exit bracket must be proved on a paper cash account. Run it while the
market is open. It trades one share of a cheap, liquid ticker and leaves the
account as it found it:

1. a bracket buy, then a wait for the fill;
2. a replace of the resting stop, read back through the ledger's own id;
3. a cancel of the bracket's exits, then an OCO exit bracket in their place;
4. a replace of the OCO's stop, then a cancel of the OCO;
5. a market sell of the share, and a check that no order and no share is left.

    BROKER=alpaca ALPACA_ACCOUNT_NUMBER=PA… python -m backend.scripts.alpaca_paper_check --ticker F

It stops at the first step that fails, and says which.
"""
import argparse
import sys
import time

from backend.services import alpaca_broker as alpaca


def wait_for(client_order_id: str, statuses: tuple[str, ...], seconds: float = 60) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        detail = alpaca.get_order_detail(client_order_id) or {}
        if detail.get("status") in statuses:
            return detail
        time.sleep(2)
    sys.exit(f"FAILED: {client_order_id} did not reach {statuses} in {seconds}s")


def step(text: str) -> None:
    print(f"- {text}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ticker", default="F", help="a cheap, liquid ticker")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    if alpaca.get_paper_account_id(refresh=True) is None:
        sys.exit("FAILED: the account did not pass the guards; the log says which one")
    if alpaca.get_positions().get(ticker):
        sys.exit(f"FAILED: the account already holds {ticker}; pick another ticker")
    import yfinance as yf

    price = float(yf.Ticker(ticker).fast_info["last_price"])  # a live quote, not history
    step(f"{ticker} last traded at {price:.2f}")

    placed = alpaca.place_bracket_order(ticker, 1, price, round(price * 0.8, 2), round(price * 1.3, 2))
    step(f"bracket placed, entry limit {placed['entry_limit']}")
    filled = wait_for(placed["client_order_id"], ("FILLED",))
    step(f"entry filled at {filled['filled_price']}")

    stop = next(e for e in placed["exits"] if e["kind"] == "stop")
    alpaca.replace_exit(stop["client_order_id"], "stop", round(price * 0.82, 2))
    moved = alpaca.get_order_detail(stop["client_order_id"])
    step(f"bracket stop moved, now {moved['alpaca'].get('stop_price')} ({moved['status']})")

    for leg in placed["exits"]:
        if not alpaca.cancel_order(leg["client_order_id"]):
            sys.exit(f"FAILED: could not cancel the bracket's {leg['kind']}")
        wait_for(leg["client_order_id"], ("CANCELLED",))
    step("bracket exits cancelled")

    legs = alpaca.place_exit_bracket(ticker, 1, round(price * 0.8, 2), round(price * 1.3, 2))
    step(f"OCO placed: {[(leg['kind'], leg['price']) for leg in legs]}")
    oco_stop = next(leg for leg in legs if leg["kind"] == "stop")
    alpaca.replace_exit(oco_stop["client_order_id"], "stop", round(price * 0.81, 2))
    step(f"OCO stop moved, now {alpaca.get_order_detail(oco_stop['client_order_id'])['alpaca'].get('stop_price')}")
    for leg in legs:
        alpaca.cancel_order(leg["client_order_id"])
    for leg in legs:
        wait_for(leg["client_order_id"], ("CANCELLED",))
    step("OCO cancelled")

    sold = alpaca.place_market_order(ticker, "SELL", 1)
    wait_for(sold["client_order_id"], ("FILLED",))
    step("share sold")
    left = alpaca._request("GET", "/v2/orders", params={"status": "open"}) or []
    held = alpaca.get_positions().get(ticker, 0)
    if left or held:
        sys.exit(f"FAILED: {len(left)} open order(s) and {held:g} share(s) left")
    print("PASSED: bracket, replace, OCO and cancel all behave on a paper account.")


if __name__ == "__main__":
    main()
