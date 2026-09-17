# Running it yourself

**This is a working experiment, not a product.** It has been run on exactly one machine, and the parts most likely to break are the ones specific to that machine. This page is honest about which those are.

## What you need before you start

| | Why | Can you skip it? |
|---|---|---|
| **Docker** | The app ships as one image | No |
| **Webull sandbox credentials** | The agent's account, and real-time quotes | **No.** The agent refuses to run without them |
| **The account number to trade** | Names which simulated account this container owns | **No.** Without it the app places no order at all |
| **A model** | Either a local GPU pool or a hosted API key | No, but either works |
| A Discord webhook URL | Notifications | Yes — the site is identical without it |
| A FRED API key | Macro series | Yes, but the news analyst then infers rates from headlines and says so in its own report |

**The hardest requirement is the model**, and the honest options are:

- **A local GPU pool.** What this runs on. Seven 8 GiB cards, and one analysis takes about 19 minutes. Free to run beyond electricity, and slow.
- **A hosted API.** Gemini Flash-Lite does the same analysis in 1.2 to 1.6 minutes. At the measured token counts that is about **$0.056 an analysis** — roughly **$10 a month** for a nine-ticker watchlist analysed about once a day, which is a rough sizing figure now that nothing forces a fixed daily count. Fast, and it costs real money.
- **A hosted API on a free tier.** Several providers serve an OpenAI-shaped endpoint with a daily token allowance and no card. Measured here on Cerebras: a full analysis in **153 seconds**, against about 19 minutes locally, with prices matching the real closes exactly. Fast and free, and rate-limited — see the throttle note below.

Nothing else in the design cares which you pick.

## The shortest path

```sh
git clone --recurse-submodules https://github.com/nandyalu/ten-acre
cd ten-acre
docker build -t trading-experiment:local .
cp compose.example.yaml compose.yaml
cp .env.example .env
# fill in .env, then:
docker compose up -d
```

`compose.example.yaml` has every setting commented, and reads every value from `.env`, so it holds no secret of its own.

The container applies its own database migrations at startup. There is nothing to run by hand.

**Then open the dashboard.** A deployment that is not ready sends you to `/setup`, which lists what is still missing and the exact lines to paste for each one. It reports whether a thing is configured and never what it is configured to, so nothing on that page can leak a key. Switching the agent on from the settings page is the last step, and the day you do it becomes day one of the experiment.

## Every environment variable

### Required

| Variable | What it does |
|---|---|
| `WEBULL_SANDBOX=1` | **Points at the sandbox.** The agent checks this itself and refuses every order without it |
| `WEBULL_APP_KEY` | From Webull's developer portal |
| `WEBULL_APP_SECRET` | Likewise |
| `WEBULL_ACCOUNT_ID` | **Which simulated account this container owns**, as the account number (`DE…`) or the internal account id. No default, deliberately — see below |
| `WEBULL_ACCOUNT_CLASS` | `INDIVIDUAL_CASH`. A cash account refuses a short outright |
| `WEBULL_OPENAPI_TOKEN_DIR` | Where the exchanged token is cached. Put it on the data volume so a redeploy reuses it |

**`WEBULL_ACCOUNT_ID` has no default and that is the point.** Three checks already narrow the sandbox's accounts to one — the sandbox flag, the `DE` number prefix, and the account class — and they narrow to the *same* one for every deployment applying the same rule. Two containers would then trade a single book, and afterwards nothing could say which of them placed an order. A default would restore exactly that. Leave it empty and the app places no order at all, which is the honest failure.

### The experiment's own numbers

| Variable | Default | What it does |
|---|---|---|
| `AGENT_BUDGET` | `10000` | What the agent starts with |
| `RESEARCH_PRICE_USD` | `0.05` | What one analysis costs it. `0` makes research free |
| `EXPERIMENT_START_DATE` | — | **Leave it empty.** The date is stamped automatically the first time the agent is switched on |

**You do not normally set the start date.** Switching the agent on is a deliberate act by a person on a date they chose, so that day is written down and never overwritten afterwards. Everything on the site that says "since" or "day N" reads it, and so does the floor for the 1-minute bar backfill — without that, a deployment started next year would page back a year of minute bars to reach a date belonging to somebody else's run. Set the variable only to state a date the stamp cannot know, such as a deployment restored from a backup.

The budget and the research price are only defaults for an unset setting — the settings page wins once anyone changes them. They matter because a fresh database has neither, and a container coming up on the wrong budget would have to be corrected by hand on its first run.

### The model

| Variable | Default | What it does |
|---|---|---|
| `TRADINGAGENTS_LLM_PROVIDER` | `ollama` | `ollama`, `google`, `openai_compatible`, `anthropic` and others |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Where the local pool is. Only for `ollama` |
| `GOOGLE_API_KEY` | — | Only for `google` |
| `TRADINGAGENTS_LLM_BACKEND_URL` | — | The endpoint, for `openai_compatible` |
| `OPENAI_COMPATIBLE_API_KEY` | — | Its key, when the endpoint wants one |
| `TRADINGAGENTS_DEEP_THINK_LLM` | — | The model name. Both stages share one value |
| `TRADINGAGENTS_QUICK_THINK_LLM` | — | Set it to the same thing |
| `TRADINGAGENTS_MAX_CONCURRENT_ANALYSES` | `1` | **Set this to your GPU count.** See below |
| `TRADINGAGENTS_MAX_DEBATE_ROUNDS` | `1` | One. Four costs 2.3x the wall clock and changed nothing measurable |
| `TRADINGAGENTS_MAX_RISK_ROUNDS` | `1` | Likewise |

### Optional

| Variable | What it does |
|---|---|
| `DISCORD_WEBHOOK_URL` | Notifications. A webhook, not a bot — the app only posts. Leave unset for a Discord-free deployment |
| `FRED_API_KEY` | Free from fred.stlouisfed.org. Without it the news analyst infers rates and the yield curve from headlines |
| `LLM_TRACE_DIR` | Writes every LLM call to disk, about 0.4 MB an analysis. A dataset of past runs cannot be collected afterwards, which is why it is on before anyone has decided to train anything |
| `PUBLIC_MODE` | See "Publishing it" below |
| `TRADINGAGENTS_MEMORY_LOG_PATH` | **Put this on the data volume.** It defaults inside the container, where a redeploy deletes it — and it holds every past decision plus the reflection written once the outcome was known |

## Running without a GPU pool

Point it at Gemini instead:

```
TRADINGAGENTS_LLM_PROVIDER=google
GOOGLE_API_KEY=...
TRADINGAGENTS_DEEP_THINK_LLM=gemini-3.5-flash-lite
TRADINGAGENTS_QUICK_THINK_LLM=gemini-3.5-flash-lite
TRADINGAGENTS_MAX_CONCURRENT_ANALYSES=4
```

Two things measured here that are worth knowing:

**Price input and output separately. Never use a blended rate.** List is $0.30 per 1M input and $2.50 per 1M output — an 8x gap — so a blended figure is a function of the completion share rather than a property of the model. Blending once produced a cost estimate 39% too low.

**`gemini-2.5-flash-lite` is retired** and returns `404 NOT_FOUND` on a current key. Use `3.5-flash-lite` or `3.1-flash-lite`.

### Any other OpenAI-shaped endpoint

Cerebras, vLLM, LM Studio, or a relay — all the same three lines:

```
TRADINGAGENTS_LLM_PROVIDER=openai_compatible
TRADINGAGENTS_LLM_BACKEND_URL=https://api.cerebras.ai/v1
OPENAI_COMPATIBLE_API_KEY=...
LLM_MODEL=qwen-3.8-27b
```

**Check the daily token allowance before the requests-per-minute one.** A full analysis spends roughly 130,000 tokens, so an allowance is what decides how many analyses a day you get, and the two models on one provider's free tier differed by a factor of eighty on exactly that: one allowed about 7 analyses a day, the other about 600. The rate limit only decides how fast, and the app already waits when it is hit.

**A free tier will return `429`, and that is handled.** The app reads the provider's own `retry-after` header and waits exactly that long rather than guessing, then retries up to four times. Guessing is not a small error: one vendor asks for 58 seconds, where an invented ladder of 5, 10 and 20 seconds would exhaust every retry before the window even reopened. Repeated pauses in the log are the throttle working, not a fault.

**A provider that sends no rate-limit headers needs its limits stated.** Gemini is one, so the app cannot see a limit coming. Set `LLM_REQUESTS_PER_MINUTE`, `LLM_TOKENS_PER_MINUTE` and `LLM_REQUESTS_PER_DAY` to the provider's published numbers. The app then waits before a call that would pass a per-minute limit, and refuses a call past the daily limit. When too few requests are left today for an analysis, the agent is told so and nothing is charged. `LLM_DAY_TIMEZONE` says where the provider's day starts; the default is Google's, midnight Pacific time.

**Gemini returns its thinking only at a stated level.** Set `TRADINGAGENTS_GOOGLE_THINKING_LEVEL` to `low`, `medium` or `high`. Without it the model does not think, and the record of each decision has no reasoning to read.

**Gemini's search-grounding tool needs a paid Google Cloud project.** Set `TRADINGAGENTS_GOOGLE_SEARCH_GROUNDING` to `true` to let the news analyst attach it. Left unset (the default), the news analyst never asks for it. Confirmed live: the Free tier gives Gemini 3 zero grounding quota, so turning this on without a billing account linked (Google's Tier 1) fails every news-analyst run under the Google provider with `429 RESOURCE_EXHAUSTED`.

## Choosing a model, if you are running locally

The rule this project learned the expensive way: **speed rules a model out; behaviour rules it in.**

Five small models were rejected here, all for the same failure. They do not error — they answer fluently, having invented the data. The tell is the token count: a model that never fetched anything spends 42–45k prompt tokens where a working one spends over 100k.

One of them read plenty and still made up every price, from different years in different runs. Its model card claimed tool calling as a strength. Both things were true, and neither predicted anything.

**Test any candidate on a real analysis before trusting it**, and check that the prices in its market report match the actual close.

## Concurrency

**Set `TRADINGAGENTS_MAX_CONCURRENT_ANALYSES` to your number of GPUs, and not more.**

One analysis runs its four analysts at the same time, then the debate and the trader in turn. So it uses up to four cards for about three minutes, and one card for the rest. N concurrent analyses is what fills N cards. The Ollama proxy queues a request when every card is busy, so the short burst of analyst requests waits and does not fail.

Measured on this hardware: fourteen at once and seven at once take **the same total wall clock**, and fourteen doubles the latency of each. The CPU saturates before the GPUs do, because this model family keeps its per-layer embeddings in host RAM. More concurrency past your card count buys nothing.

## Publishing it

The site is meant to be read by anyone. The small write surface is not.

**Run two containers over one volume.** The private one, as configured above. A second with `PUBLIC_MODE=1`, and point your tunnel at that one.

`PUBLIC_MODE` does two things, and the second matters more:

1. **Every write is refused.** Middleware, not a per-route check, so it also covers whatever route gets added later.
2. **No scheduler, no Discord, no trade stream.** Without this, two containers over one database would each run its own agent on the same book — duplicate research commissions paying twice for the same look, duplicate decision passes, two sets of orders at the broker against one ledger. **None of that arrives as an HTTP request**, so refusing writes alone would not have stopped any of it.

Mount the volume read-write for the public copy. SQLite writes its `-wal` and `-shm` sidecars even to read, and `:ro` fails to open the database at all. The guarantee is `PUBLIC_MODE`, not the mount flag.

## When something goes wrong

**"pull access denied for trading-experiment"** — the image tag has no registry prefix, so Docker resolves it to Docker Hub. Set `pull_policy: never`.

**Every analysis fails with "No available vendor"** — the model asked for an indicator that does not exist, three times, and tripped the vendor circuit breaker. Fixed in this repo: bad arguments no longer count as vendor ill-health. If you see it on an older checkout, that is the cause.

**Reddit `429 Too Many Requests`** — expected on the RSS feed. The sentiment analyst reads a public RSS feed with no key, and a throttled subreddit is marked unavailable. At high concurrency it happens a lot, and the sentiment analyst then works with less data than it would at low concurrency. If you run trawl, set `REDDIT_TRAWL_URL` to avoid most of them. See [setup](setup.md#reddit).

**The agent never trades** — **open `/setup` first.** It checks every requirement in one place and names the ones that are missing, which is faster than reading logs. The usual answers are `WEBULL_SANDBOX=1`, an empty `WEBULL_ACCOUNT_ID`, a model endpoint that does not answer, or the agent never having been switched on in Settings.

**The model dropdown in Settings is a plain text box** — the app could not list what your endpoint serves, so it stopped guessing and let you type the name. Two causes seen here, both on hosted endpoints: the request carried no API key, or the provider blocked it for having no browser-like `User-Agent`. Neither stops an analysis running; only the dropdown is lost.

**Orders fill but have no stop** — Webull refuses a bracket while cash is unsettled, which happens whenever the agent sells to fund a buy. The app falls back to a plain order plus separately-armed exits, and that second step can fail. The site flags the position and offers a button that rests the missing exits.

**A model reports prices from the wrong year** — it never fetched anything. See "Choosing a model" above. This is not a prompt problem and no amount of instruction fixes it.
