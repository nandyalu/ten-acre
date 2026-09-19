---
paths:
  - "backend/services/{bars,listings,positions,quotes,intraday,watchdog}.py"
  - "backend/scripts/backfill_intraday_bars.py"
  - "TradingAgents/tradingagents/dataflows/**"
---

<!-- Moved from CLAUDE.md on 2026-09-13. This file loads when Claude reads a file that matches paths. -->

## Market data goes through the bar cache

`backend/services/bars.py` is a read-through cache over the `dailybar` table (`(ticker, date)`). **Route any new daily-history read through `bars.get_bars()`, not `yf.Ticker(...).history()` or a direct Webull call** — the whole point is that a completed session never changes, so refetching one is waste and rate-limit risk.

**Webull first, yfinance as fallback (2026-09-08).** `bars._fetch_history` tries `_fetch_from_webull` (the same history-bar endpoint `backend/services/intraday.py` uses for 1-minute bars, called here with `Timespan.D`) and falls through to `_fetch_from_yfinance` only when that returns `None` — Webull not configured, the call failing, or coming back empty. Confirmed live: the endpoint pages back daily bars with no real depth ceiling, over 2,000 bars deep in testing. yfinance is not removed — it is what already produces this app's "possibly delisted" false positives and 429s, and a Webull outage must not take the whole daily cache down with it.

Three legitimate direct yfinance uses remain, none of them history: `positions.get_current_price` (a live quote, Webull's fallback), `watchdog.get_next_earnings_date` (the calendar), and `fundamentals.describe` (`Ticker.info` and the quarterly income statement, for the agent's `fundamentals` fetch, since 2026-09-19). The last one is on yfinance because the Webull sandbox host has no route for its `/openapi/fundamentals/` family: every call answered "404 Route Not Found". Do not add a client against Webull's production host for it; that would be the first live-host code in the repo, and nobody has approved a production call.

Non-obvious rules the cache depends on:

- **Today's bar is never stored.** It is still moving. `include_today=True` gets it via a separate live request instead. **A page must not pass it inside a loop over tickers.** Each call sends one vendor request at three seconds a call, and on 2026-09-14 a loop over four tickers made `/api/agent/curve` take 12 seconds. One ticker on a page, as in `positions.get_price_history` and the SPY baseline, costs one request. `agent_book.equity_curve` prices today from the price cache (`positions.get_shown_price`).
- **Pass `today=` when the caller has a market-relative date.** The watchdog does: after about 8pm ET the local clock is already tomorrow, so the default would treat the just-closed session as still in progress.
- **`_earliest_attempt` records what was asked for, not what came back.** Without it, a ticker with less history than requested refetches on every call forever.
- **`last_completed_session` ignores holidays deliberately.** The 30-minute recheck throttle absorbs the resulting extra request.

The table is pure cache; dropping it costs only a refetch.

## Tickers that stop trading

`backend/services/listings.py` marks a ticker inactive once no fresh bar has appeared for `STALE_AFTER_TRADING_DAYS` (7). Every fetch path checks it: the bar cache, `get_current_price`, and the watchdog's tracked list.

**Why it needs detecting at all:** a delisted symbol does not fail cleanly. AILEQ returned five bars across two months, every one priced at $0.000001. Nothing in that looks like an error — to the bar cache it was a ticker merely behind, so it refetched every 30 minutes forever, and the (now-retired) daily sweep spent minutes of GPU analyzing a company with no market, then could not record the signal because there was no price to record it against.

**The rule is freshness, not price.** A real penny stock at $0.0001 is still real and must keep working; a price threshold would wrongly exclude it.

An inactive ticker is still rechecked once a day, so a lifted halt recovers without anyone noticing. `/ignore` and `/unignore` are the manual override, and a manual setting is never overwritten by detection.

A held position stays in the portfolio — there is just nothing to fetch, and its lots are excluded from the vs-SPY comparison like any other undateable lot.

## Reddit/social-sentiment data source

`TradingAgents/tradingagents/dataflows/reddit.py` reads Reddit's public RSS search feed (no API key). A `429` warning in the logs is expected. The fetcher waits and retries once per run, then marks that subreddit `<unavailable: fetch failed, not an absence of posts>`. **A failed fetch never reads as "no posts found"** (upstream `7cc478a`, taken 2026-09-13), because the sentiment analyst must not treat throttling as silence. It is not a bug unless it fails on *every* run.

**With `REDDIT_TRAWL_URL` set, the fetcher loads Reddit's HTML search page through the trawl container, and uses the RSS feed only when trawl fails.** On 2026-09-13, measured from this host, the RSS feed returned `429` after its first request, and 18 HTML search pages loaded through trawl 2 s apart all succeeded. The notes below constrain a future edit:

- **Never use Reddit's JSON search through trawl.** It loads, but it returns far fewer results: 0 posts for GOOG in r/stocks where the HTML page showed 14. A short answer that looks like success is worse than a `429`. A single post's JSON is complete, which is why post bodies come from it.
- **The parser returns `None` for a page it does not recognize.** It returns `[]` only when Reddit's `search-error-message` block says it found no results. A Reddit redesign must read as unavailable, never as silence.
- **The search page has no post bodies.** The fetcher reads a body for every post it shows, one trawl request each, because the RSS feed carries a body for every post. On 2026-09-14 a first version read only the top 2 by score, and a same-moment comparison showed it gave less body text than RSS: 2 of 3 bodies in AAPL r/stocks. The same comparison found identical 7-day post sets on both paths in all 4 subreddits where both worked.
- **Only the trawl path needs the 7-day filter.** The RSS feed respects `t=week`: its oldest post in that comparison was 6 days old. The search page ignores it and returned posts from as far back as March.
- **`skipHttp` and `maxTier: 3` are deliberate.** Tier 1 is a plain fetch from this IP, which Reddit throttles like the RSS feed. Tier 4 needs a paid residential proxy.
- **The live container reaches trawl at `http://host.docker.internal:8191`.** Trawl is on its own Docker network, `flaresolverr_default`. `stocktwits.py` is in the same directory, and the sentiment analyst fetches it on every run, beside Reddit. Its read stops at 5 MiB, the same limit as the Reddit feed (upstream #1328, taken 2026-09-14).

The Webull OpenAPI SDK (`backend/services/quotes.py`, `backend/services/sandbox_broker.py`) is market-data + brokerage only (quotes, fundamentals, financials, trading) — it has no news-article or social-sentiment endpoints, so it can't replace `get_news`/`reddit.py`.
