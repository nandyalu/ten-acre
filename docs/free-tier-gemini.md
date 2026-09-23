# Running on Google's free tier

**Not built. This is a study for later.** It asks whether the whole experiment could run on Google's free Gemini tier, with no Ollama pool and no local GPU. On the demand measured so far, the answer is yes, but the app needs rewiring first. Nothing on this page describes how the app works today.

Written 2026-09-13. The limits below are what Google AI Studio showed for one free key on that date. Google can change them at any time.

## The idea

- **The agent** runs on Gemini 3.8 Flash. When that model's requests for the day run out, it moves to 3.7 Flash, then to 3.6 Flash. That gives 60 requests a day.
- **Analyses** run on Gemini 3.5 Flash Lite, and move to Gemini 3.1 Flash Lite when that model's requests run out. That gives 1,000 requests a day. (Gemini 3.5 Flash, without "Lite", allows only 20 requests a day, so this page means the Lite models.)
- **Gemma 4 26B and 31B** are the other free models with room, and they are considered too.

On the local pool, time is the limit: one analysis takes about 18 minutes. On the free tier, requests per day and requests per minute become the limits instead.

## The free limits

| Model | API name | Requests a minute | Tokens a minute | Requests a day |
|---|---|---|---|---|
| Gemini 3.8 Flash | `gemini-3.8-flash` | 5 | 250K | 20 |
| Gemini 3.7 Flash | `gemini-3.7-flash` | 5 | 250K | 20 |
| Gemini 3.6 Flash | `gemini-3.6-flash` | 5 | 250K | 20 |
| Gemini 3.5 Flash Lite | `gemini-3.5-flash-lite` | 15 | 250K | 500 |
| Gemini 3.1 Flash Lite | `gemini-3.1-flash-lite` | 15 | 250K | 500 |
| Gemma 4 26B | `gemma-4-26b-a4b-it` | 30 | 16K | 14,400 |
| Gemma 4 31B | `gemma-4-31b-it` | 30 | 16K | 14,400 |

- **Each limit belongs to one model on one key.** Two models do not share a budget.
- **The daily count starts again at midnight Pacific time**, which is 3:00 AM Eastern. A full day's budget is ready before the market opens.
- **The API names come from the model list this key returned** on 2026-09-13.

## What the experiment actually uses

Measured on the main deployment from 2026-09-03 to 2026-09-12. The Gemini 3.5 Flash Lite figures come from 57 analyses in the earlier two-book deployment's database. Token counts for single calls are estimated from trace files at four characters a token.

### The agent

| | Measured |
|---|---|
| Passes a day | 3 to 11 |
| Model calls a pass | 1 call in 40 of 45 passes, 2 calls in 4, 3 calls in 1 |
| Model calls on the busiest day | 11 (2026-09-04) |
| Tokens a call, with the system prompt | about 5,000 to 6,000 in |
| Largest single prompt | 17,493 characters, about 4,400 tokens without the system prompt |

**Most of those passes ran before research moved inside the pass on 2026-09-12.** A pass that orders research now makes at least two calls, and 2026-09-12 had 7 calls from 3 passes. The most calls one pass can make is 9: three act turns, three read turns, and one refusal retry on each act turn.

On Gemini 3.5 Flash Lite at `thinking_level=high`, one two-turn pass on 2026-09-13 used 8,592 tokens in and 2,995 out.

### Analyses

| | Measured |
|---|---|
| Analyses a day | 1 to 11 |
| Model calls on the busiest day | 226 (11 analyses, 2026-09-10) |
| Calls an analysis, Gemini 3.5 Flash Lite | 20.4 on average |
| Tokens an analysis, Gemini 3.5 Flash Lite | 104K in, 9.5K out |
| Time an analysis, Gemini 3.5 Flash Lite | 70 seconds, with no rate limit in the way |
| Largest single call in an analysis | about 12K tokens on gemma4-e4b (52 analyses); about 28K tokens on qwen-3.8-27b (14 analyses) |

## Does it fit?

### The agent on three Flash models: yes, on an ordinary day

| Day | Calls | Fits 60 a day? |
|---|---|---|
| The busiest measured day | 11 | Yes |
| 11 passes at 3 calls each | 33 | Yes |
| 20 wakeups at 3 calls each | 60 | Exactly, with nothing left over |
| One pass at the 9-call ceiling | 9 | Uses almost half of one model's day |

- **One model was enough on every measured day.** The agent never needed 20 calls in a day, so rotation matters only on a busy day.
- **Five requests a minute is enough.** A pass's turns run one after another, and research inside a pass puts minutes between them.

### Analyses on two Flash Lite models: yes, with a wide margin

- **One model covers the busiest measured day.** 11 analyses at about 21 calls is about 230 requests, and the limit is 500.
- **Two models allow about 47 analyses a day.** Cash, not a count, bounds research. Faster analyses could make the agent order more than it does now, and that is not measured.
- **Fifteen requests a minute slows an analysis a little.** An analysis makes 20 calls in 70 seconds, which is 17 a minute, so the throttle stretches it to about 90 seconds.
- **Run one analysis at a time.** Two at once share the same 15 requests a minute.
- **250K tokens a minute is not a limit** for one analysis of about 114K tokens.

### Gemma 4: possible for the agent, and not faster than the GPUs for analyses

- **For the agent, Gemma 4 31B could replace rotation completely.** A call of about 6,000 tokens fits inside 16K tokens a minute, and 14,400 requests a day is more than any day could use. The agent would make every decision on one model.
- **For analyses, 16K tokens a minute is the limit.** An analysis of about 130K tokens needs at least 8 minutes of that budget, so at most about 7 analyses an hour. The local pool does about 23 an hour on seven cards.
- **One analysis call can be larger than 16K tokens.** None of the 52 gemma4-e4b analyses had one, but 12 of the 14 qwen analyses did. A call larger than the per-minute limit is probably refused, not delayed. That is not measured.

## What would have to change

Today one setting, `llm_model`, chooses the model for the agent and for every analysis, and one rate-limit budget covers the whole process. Rotation needs all of the following.

1. **A model setting for each role.** Done on 2026-09-23 for the tool channel: `AGENT_DECISION_MODEL` names the agent's model, and its text fallback asks the same one. The JSON channel still uses the analysis model.
2. **An ordered list of models for each role, with limits for each model.** `llm_throttle` holds one budget, read from `LLM_REQUESTS_PER_MINUTE`, `LLM_TOKENS_PER_MINUTE` and `LLM_REQUESTS_PER_DAY`. Google sets its limits per model, so the budget must be per model too. Since 2026-09-23 a separate decision model has its own bucket (`AGENT_LLM_*`). The ordered list of fallback models is not built.
3. **Choose the model before the work starts, and never switch during it.** An analysis that starts on one model and finishes on another is a signal from two models. An analysis starts on the first model with room for about 25 requests. A pass starts on the first model with room for its ceiling of 9 calls.
4. **Treat Google's own daily refusal as final.** A 429 that names a per-day quota means that model is spent until midnight Pacific, even when the app's own count says otherwise.
5. **Show the agent every analysis, whichever model made it.** `agent._recent_signals()` keeps only signals from `analysis.get_model()`. That filter exists to keep a comparison sweep out of the live book. With two analysis models, the agent would not see the second model's analyses. With a separate agent model, it would see no analyses at all. The filter must follow the list of analysis models instead.
6. **Record which model made each decision.** Every `Signal` stores its model, but `agentrun` does not, so a rotated pass could not be attributed afterwards. It needs a `model` column. That changes what the record holds, so it needs a `JOURNEY.md` entry.
7. **Test every model before it is used.** None of the three Flash models, and neither Gemma 4 model, has run this app. Each needs the `model-change` acid test: it must fetch data with tools and carry the real prices through, use the candidate menu, and fail structured output at most once a run. For Gemma through the Gemini API, also check that system instructions, JSON output and thinking work at all.
8. **Show the lists on the setup and settings pages**, with the requests each model has left today.

## What it does to the experiment

**Rotation mixes models inside the record, and not at random.** The first model of the day makes the morning's analyses and the second makes the evening's. A difference between the two models would also be a difference between times of day. The scorecard's `by_model` breakdown keeps the signals apart, but it cannot remove that bias. `CLAUDE.md` states the rule this touches: switching models teaches you nothing if the win rates blend.

So, in order of preference:

1. **One model for each role, all day.** On measured demand this fits: Gemini 3.5 Flash Lite for analyses, and one Flash model or Gemma 4 31B for the agent.
2. **A second model only as an overflow.** Record it on every row it touches, and keep it rare enough to leave out of a comparison.
3. **Full rotation last.** Only with a `JOURNEY.md` entry saying the record from that date mixes models.

## Other risks

- **Google can change the free limits at any time.** On 2026-09-13 the same table already listed Gemini 3.1 Pro at 0 requests a day.
- **A popular new model can be unavailable for hours.** On 2026-07-30 `gemini-3.5-flash` returned `503 UNAVAILABLE` for about 12 hours, while `gemini-3.1-flash-lite` kept working.
- **Google's terms for unpaid use let it use prompts and responses to improve its products.** This project sends public market data and a simulated book, and nothing private.
- **Thinking costs output tokens, not requests.** At `thinking_level=high` a pass wrote about 1,300 to 2,000 output tokens in the 2026-09-13 probes, against 110 with no thinking level. The daily counts do not change.

## What it would buy

- **No local hardware.** No seven-card pool, no 700 W while it works, and no contention for host memory.
- **An analysis in about 90 seconds instead of about 18 minutes.** The agent could order research and act on it within the same few minutes.
- **A deployment anyone can run with one free key.**
