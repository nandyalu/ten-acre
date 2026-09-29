---
paths:
  - "backend/services/{alpaca_broker,broker}.py"
  - "backend/scripts/alpaca_paper_check.py"
  - "compose.example.yaml"
---

## Why a second broker

**Portability, not risk.** Webull OpenAPI needs a funded brokerage account, a separate access application, and one of three regions. That keeps other people from running this experiment. Alpaca paper keys need an email address and nothing else. Added 2026-09-26, item 8 of PLAN.md's "Later" list.

**`BROKER` picks the module, and `broker.py` is the only thing callers import.** `webull` is the default, so an existing deployment changes nothing. The wrappers in `broker.py` are plain functions, not a module `__getattr__`: 81 tests patch `agent.broker.<name>`, and an attribute that `monkeypatch` restores onto a module with `__getattr__` stays pinned after the test. `broker.is_paper()` replaced `quotes.is_sandbox()` at every gate in `agent.py` and the two routes.

## The four guards in Alpaca's terms

`CLAUDE.md` states them. Each one answers the same question as its Webull twin.

1. **The paper host is the only host in the module.** `PAPER_HOST` is a constant, and `_assert_sandbox()` checks `_HOST` against it before every order. Paper keys and live keys are different pairs, and the paper host refuses a live key. `test_only_the_paper_host_is_written_anywhere` fails on any `://api.alpaca.markets` in `backend/`, and on any Alpaca URL outside `alpaca_broker.py`.
2. **`PA` prefix.** Every paper account number starts with it.
3. **The class comes from the account.** A multiplier of 1 is `cash`, more is `margin`, and `ALPACA_ACCOUNT_CLASS` must agree. A new paper account is margin with a multiplier of 4. `shorting_enabled` must be false.
4. **`ALPACA_ACCOUNT_NUMBER`, no fallback.** Two deployments on one key would otherwise share a book.

## How the module hides Alpaca's differences

- **A replace makes a new order.** Alpaca marks the old one `replaced` and points `replaced_by` at the new one. `_current` follows the chain, so the ledger's `client_order_id` still finds what rests now. Proved on paper 2026-09-26: a held bracket stop moved from 320 to 322 and read back through the old id.
- **Status words.** `_STATUS` maps to `FILLED`, `PARTIAL_FILLED`, `CANCELLED`, `EXPIRED`, `REJECTED`, and `SUBMITTED` for the rest (`new`, `accepted`, `held`, `pending_*`).
- **A bracket is `gtc`.** Its legs share its time in force, so a `day` bracket's stop and target would expire at the close. Webull's master cannot be GTC, which is why that module differs here.
- **One exit makes an `oto`.** Alpaca's `bracket` needs both a stop and a target.
- **Unsettled cash** is `cash − buying_power` on a cash account, an inference to check once a sell settles on paper. Alpaca does not refuse a bracket against unsettled funds, so Webull's fallback path never runs.
- **No trade stream.** `trade_stream.start` returns at once under Alpaca. Fills that happen on their own settle on the 15-minute poll.
- **The candidate screen comes from Alpaca's data host (2026-09-29).** There is no Webull client, so without it the menu was empty, the prompt dropped every research rule, and the agent waited in cash for signals it had no way to order. `DATA_HOST` is the one other host in the module, and only `_market_data` reaches it, with a GET. `most_active` and `day_gainers` give symbols. `get_snapshots` prices them in Webull's row shape on the `delayed_sip` feed: free keys may not read live SIP, and IEX volume is about 4% of the market, which fails the volume floor. It drops a symbol whose last daily bar is more than 5 days old, because Alpaca answers a delisted symbol (EA, LBRDK) with its last bar where Webull refuses it. Alpaca's most-actives includes ETFs, so `candidates._listed` removes them with Nasdaq Trader's symbol directory, which also gives the names.

## What is proved and what is not

**Proved on 2026-09-26, with the market closed**, on account `PA3RKTJ5XU6E` after its multiplier was set to 1: the guards pass, a bracket is accepted as `gtc` with both legs `held`, a held leg can be replaced, a cancel of the entry cancels both legs, and a sell of shares not held is refused before any request.

**Not proved: fills, and an OCO on a real position.** Run `python -m backend.scripts.alpaca_paper_check` during a session before any deployment trades through Alpaca. It buys one share, moves and replaces every exit, sells, and checks the account is clean.
