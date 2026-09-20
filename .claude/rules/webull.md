---
paths:
  - "backend/services/{sandbox_broker,quotes,intraday,trade_stream,setup_check}.py"
  - ".env.example"
---

## The four guards, and why each one is written the way it is

`CLAUDE.md` states the four guards as rules. This section holds the reasoning behind them, which a future edit must not undo.

**The prompt may lie to the model. The code must never lie to itself.** Since 2026-09-09 the prompt tells the agent "You manage a small account of real money", because an agent that knows the stakes are imaginary is not asked the question this experiment exists to ask. The four checks below are the code's own knowledge of what it is connected to.

- **`_assert_sandbox()`** runs immediately before every order, not once at import, so a change to the environment mid-process cannot leave a live client armed.
- **The `DE` account-number prefix check.** Every simulated account on the sandbox host is DE-prefixed, in both the DEM and DEL series. The widening from `DEM` to `DE` on 2026-09-03 corrected a wrong observation and was not a relaxation.
- **The account-class check.** The target is resolved by `account_class == INDIVIDUAL_CASH`, never hardcoded.
- **`WEBULL_ACCOUNT_ID` names the one account this deployment owns.** An unset or empty value stops order flow and never falls back to anything. It takes the account number (`DE…`) or the internal id, and it applies after the other three checks, so it can only narrow what they allowed. There is no default on purpose: a person must write down which book the container owns.

**The day someone relaxes one of them because the agent thinks the money is real anyway is the day this becomes dangerous.**

## Webull OpenAPI reference

**<https://developer.webull.com/apis/llms.txt>** — an LLM-oriented index of the whole OpenAPI documentation, with links to every endpoint page. Read it before guessing at a Webull payload shape or endpoint name. The Python SDK (`webull-openapi-python-sdk`) returns **raw dicts with no typed models**, so the field names this repo relies on were discovered from live responses, not from the package — the docs are the only other source of truth.

What the index covers, and where each maps here:

| Docs area | Used by |
|---|---|
| Account Management — Account List, Account Balance, Account Positions | `backend/services/sandbox_broker.py` (`get_positions`) |
| Market Data — snapshot, tick, depth, bars, fundamentals | `backend/services/quotes.py` (`get_realtime_price`) |
| Authentication — HMAC-SHA1 signature, client token lifecycle | `quotes.get_api_client`, token cached under `WEBULL_OPENAPI_TOKEN_DIR` |
| Order Management — preview/place/replace/cancel | `backend/services/sandbox_broker.py`, **against the sandbox only**. Every call passes `_assert_sandbox()` first. Real order execution on a production account is a standing non-goal |

### Combo orders: what the docs and `preview_order` both get wrong

The agent buys with a **bracket** — `MASTER` entry plus `STOP_PROFIT` and `STOP_LOSS` legs, one `place_order` call, one shared `client_combo_order_id`. The broker activates the exits when the entry fills, so no position is ever held with nothing resting under it. Arming afterwards always had that window: on 2026-08-13 both buys filled and neither got its exits, because the sell legs were validated while the account still held nothing and read as a new short (`GENERATE_NEW_SHORT_POSITION`).

Brackets are **not in `llms.txt`** — only the Place Order page's `combo_type` enum mentions them. Four rules were found by testing against the live sandbox, and none of them appear in the docs:

- A `MASTER` leg **cannot be `MARKET`**, and cannot be `GTC` (`INVALID_PARAMETER`). The entry is a marketable limit instead — 0.5% through the offer, `DAY` — which behaves like a market order and caps slippage.
- The exits **may be `GTC` under a `DAY` master**. They have to be: a `DAY` exit protects the position for an afternoon and then quietly stops existing.
- A stop at or above the entry limit is refused (`TRADE_STOP_LOSS_PRICE_LT_OPENPRICE`) — and the refusal takes the **buy** with it, because the legs are one submission. `agent._place` screens both levels before sending, so a bad level costs nothing.
- **Any combo is refused while the cash is unsettled** (`CANT_USE_UNSETTLE_FUNDS_FOR_COMBO_ORDER`). A plain market order may be placed against unsettled proceeds; a combo may not. Selling to fund a buy in the same pass is something the agent's prompt explicitly permits, so this is routine, not an edge case — `_place` falls back to a market order plus `_arm_exits` rather than failing the trade.

**`preview_order` cannot be used to check a combo.** It returned 200 for the `MARKET` master that `place_order` then rejected outright. Preview validates cost, not shape.

Two things the index settles that have bitten this project:

- It confirms there is **no news or social-sentiment endpoint**, so Webull cannot replace `get_news`/`reddit.py` no matter how the quota looks.
- It documents **MQTT streaming** for real-time data. This repo polls instead, which is the right call at a 1-2 week horizon, but it is the thing to reach for if intraday granularity ever matters.

Rate limits are not in the index itself — they live on the linked Market Data API Overview page. Order History is documented at 2 requests per 2 seconds.

### Confirmed from the docs (2026-08-07)

**Account Positions** returns exactly: `position_id`, `currency`, `quantity`, `symbol`, `option_strategy`, `instrument_type`, `last_price`, `cost_price`, `unrealized_profit_loss`, `event_outcome`, `legs[]`. `sandbox_broker.get_positions` reads `symbol`, `quantity` and `instrument_type`.

**There is no acquisition-date field on a position.** Not under any name. The date a position opened is only in Order History.

Three facts about Order History are not obvious and cost time to find:

- **Rows are combo wrappers, not orders.** Each row is `{client_order_id, combo_type, combo_order_id, orders: [...]}`, and a single-leg order is still wrapped in a one-element list. The top level has no symbol, no side and no quantity, so a parser that reads only the top level finds nothing. `sandbox_broker.orders_in()` unwraps the row.
- **`page_size` must be 10-100.** A value outside that range returns HTTP 417, `OAUTH_OPENAPI_PARAM_ERR`.
- **The documented "2 requests per 2 seconds" is too fast.** Requests at exactly that rate returned 429s. A pause of 2.5s between requests worked.

The code that synced a personal account from Order History (`broker.py` and `fix_import_dates.py`) was deleted on 2026-09-01 in commit `99dc4f9`, when the app moved to one simulated book.
