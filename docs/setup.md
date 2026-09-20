# Credentials

Everything the app needs, and what happens when you skip each one.

**Only the Webull sandbox keys and a model are required.** Without them the agent refuses to run, because it has no account to trade and nothing to think with. Everything else degrades to something sensible.

**You do not have to work through this page in the dark.** A deployment that is not ready sends you to `/setup`, which lists every requirement, says which are missing, and shows the exact lines to paste. It reports whether a thing is configured and never what it is configured to, so no key can leak through it.

| | Required? | Without it |
|---|---|---|
| [Webull sandbox](#webull) | **Yes** | The agent refuses to run |
| [Discord webhook](#discord) | No | No notifications. The site is identical |
| [FRED](#fred) | No | The news analyst infers rates from headlines, and says so in its own report |
| [Reddit](#reddit) | No | Sentiment comes from a public feed instead. This is the normal path |
| [An LLM](#the-model) | **Yes** | Nothing analyses anything |

## Webull

The agent's brokerage account, and real-time quotes.

1. Sign in at the [Webull OpenAPI developer portal](https://developer.webull.com/) with a normal Webull account.
2. Create an app. You get an **App Key** and an **App Secret**.
3. The portal issues sandbox and production credentials separately. **Take the sandbox pair.**

4. Sign in to the Webull paper account itself and note the **account number** of the account you want traded. It starts with `DE`.

```
WEBULL_APP_KEY=...
WEBULL_APP_SECRET=...
WEBULL_SANDBOX=1
WEBULL_ACCOUNT_CLASS=INDIVIDUAL_CASH
WEBULL_ACCOUNT_ID=DE00000000
```

**`WEBULL_SANDBOX=1` is not a suggestion.** The agent reads it itself and refuses every order without it. It is the boundary between an experiment and a machine spending real money, and it is checked in code rather than trusted to a config file.

`WEBULL_ACCOUNT_CLASS` picks which kind of sandbox account to trade. `INDIVIDUAL_CASH` is the default and the right one: a cash account refuses a short outright. A margin account would fill one, which is why the app also enforces long-only itself rather than relying on the account type.

**`WEBULL_ACCOUNT_ID` names the one account this deployment owns, and it has no default.** Leave it empty and the app places no order at all. That is deliberate. The checks above narrow the sandbox's accounts to one, and they narrow to the *same* one for every deployment applying the same rule — so two containers would trade a single book, and afterwards nothing could say which of them placed an order. It takes either the account number or the internal account id, and matches whichever you give it.

Quotes need a stock-quotes market-data subscription on the account. Without it, or after any failure, prices fall back to yfinance automatically.

## Discord

Notifications only. **A webhook, not a bot** — the app posts and never reads, so there is nothing to authenticate as.

1. In Discord, open the channel you want posts in.
2. **Edit Channel → Integrations → Webhooks → New Webhook.**
3. Name it, then **Copy Webhook URL**.

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

That is the whole setup. No application, no token, no OAuth scopes, no invite link, and no bot in your member list.

Leave it unset and the app runs with no notifications at all. Nothing else changes — every post has a page on the site that holds the same information.

## FRED

Macro series: CPI, rates, the yield curve. [Free key from the St. Louis Fed](https://fred.stlouisfed.org/docs/api/api_key.html), issued immediately.

```
FRED_API_KEY=...
```

**Worth the two minutes.** Without it the news analyst infers the macro picture from headlines and says so in its own report — "Data Limitation: due to data access constraints". Reading the actual series is the point of having the analyst.

## Reddit

**Skip this.** Leave `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` unset and the sentiment analyst reads a public RSS feed, which needs no credentials and is the supported path.

**You probably cannot set it up anyway.** Reddit ended self-serve API app creation under its [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy). New applications go through an approval aimed at products, not personal tools.

**If you run [trawl](https://github.com/germondai/trawl), set `REDDIT_TRAWL_URL`** to its address, for example `http://host.docker.internal:8191`. The sentiment analyst then loads Reddit's HTML search page in trawl's browser. That page loads where the RSS feed returns `429`, and it shows score and comment counts. The page has no post bodies, so the fetcher reads the body of each post it shows, one request each: up to 15 more requests for an analysis. If trawl fails, the search falls back to the RSS feed.

**Decide about Reddit's terms yourself.** Reddit does not allow automated access without its permission. A browser built to hide automation is a clearer case than a public RSS feed.

What each path gives you:

| | RSS (default) | trawl | OAuth |
|---|---|---|---|
| Credentials | None | None, but a running trawl container | Approval required |
| Rate limit | About 1 request a minute per IP | Not known. 18 of 18 pages loaded 2 s apart in a test on 2026-09-13 | 100 a minute |
| Post score and comment count | No | Yes | Yes |
| Post body excerpt | Every post | Every post | Every post |
| Posts older than 7 days | Filtered by Reddit | Filtered by the fetcher, because the search page ignores `t=week` | Filtered by Reddit |

**The feed searches all the subreddits in one request** (`r/a+b+c`), because it allows about one request a minute per IP and a request for each subreddit spent a back-off on nearly every run. Each entry names its subreddit, so the posts are grouped back by it. Reddit's HTML search page has no such form, so trawl loads one page for each subreddit instead, at the same time.

**On the RSS path, `429` warnings in the log are expected, not a fault.** A `429` waits for `Retry-After`, or up to 60 seconds, and retries once per run. Then the search is marked unavailable. The analyst reads "unavailable", never "no posts found", so throttling does not look like silence.

## The model

Either a local pool or a hosted API. Nothing else in the design cares which.

### A local Ollama pool

```
TRADINGAGENTS_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
TRADINGAGENTS_DEEP_THINK_LLM=gemma4-e4b-qat-128k
TRADINGAGENTS_QUICK_THINK_LLM=gemma4-e4b-qat-128k
TRADINGAGENTS_MAX_CONCURRENT_ANALYSES=1
```

Free beyond electricity, and slow — about 19 minutes an analysis on an 8 GiB card. **Set the concurrency to your GPU count and no higher**; see [Running it yourself](deploying.md#concurrency) for why more buys nothing.

### A hosted API

```
TRADINGAGENTS_LLM_PROVIDER=google
GOOGLE_API_KEY=...
TRADINGAGENTS_DEEP_THINK_LLM=gemini-3.5-flash-lite
TRADINGAGENTS_QUICK_THINK_LLM=gemini-3.5-flash-lite
```

About 1.2 to 1.6 minutes an analysis, and roughly **$0.056 each** — near $10 a month for a nine-ticker watchlist analysed about once a day, a rough sizing figure since nothing forces a fixed daily count any more.

### Any other OpenAI-shaped endpoint

Cerebras, vLLM, LM Studio, or a relay. Several of these serve a free tier with a daily token allowance and no card.

```
TRADINGAGENTS_LLM_PROVIDER=openai_compatible
TRADINGAGENTS_LLM_BACKEND_URL=https://api.cerebras.ai/v1
OPENAI_COMPATIBLE_API_KEY=...
TRADINGAGENTS_DEEP_THINK_LLM=qwen-3.8-27b
TRADINGAGENTS_QUICK_THINK_LLM=qwen-3.8-27b
```

Measured here: a full analysis in **153 seconds**, with the prices in its market report matching the real closes exactly. Leave the key out for a local server that wants none.

**Read the daily token allowance, not the requests-per-minute limit.** One analysis spends roughly 130,000 tokens, so the allowance is what sets how many you get in a day — two models on one free tier differed by a factor of eighty on that alone. When the rate limit is hit the app reads the provider's own `retry-after` header and waits exactly that long, so `429` lines in the log are the throttle working. A provider that sends no rate-limit headers, such as Gemini, needs its limits stated in `LLM_REQUESTS_PER_MINUTE`, `LLM_TOKENS_PER_MINUTE` and `LLM_REQUESTS_PER_DAY` — see [Running it yourself](deploying.md#any-other-openai-shaped-endpoint).

Both stages take the same value. The model is also a database setting, so the settings page changes it without a redeploy; these variables only supply the starting value.

**Choose on behaviour, not speed.** Five small models were rejected here for inventing prices they never fetched, and the fastest of them was the worst. See [Running it yourself](deploying.md#choosing-a-model-if-you-are-running-locally).

## Logs

The app writes to `logs/ten-acre.log` in its data directory, beside the database, rotating at 5 MB across ten files — over a month at the volume this produces. Files written before 2026-09-17 are named `trading-experiment.log`, the app's old name.

It writes to standard output too, so `docker logs ten-acre` works. **The file exists because that output does not survive the container.** Rebuild the image and every line is gone.

That loss is not theoretical. Two positions were once found holding no exits, and the run that placed them had already been erased — so why the exits never rested had to be reconstructed from prices and ledger rows instead of read from the line the code had already written.

```bash
docker exec ten-acre tail -f /app/data/logs/ten-acre.log
docker exec ten-acre grep INTC /app/data/logs/ten-acre.log
```

Every logger is named `ten-acre.<module>`, so grepping `ten-acre.agent` gives the decision passes and nothing else.
