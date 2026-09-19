# Contributing

**Read this first. It is short on purpose.** The reference documents in this repo run to well over a thousand lines, and nobody arriving for the first time should have to read them to avoid breaking something important.

## What this is

**Ten Acre is an experiment, not a product.** One AI agent trades one simulated account with $10,000, chooses its own research, pays for it out of the same money, and nobody helps it. The question being asked is what an agent does when it is given real tools and no human hand.

That framing decides most arguments about this codebase, so it decides what a good contribution looks like. A change that makes the app more useful to operate is usually a change that makes the experiment worth less.

## Four rules that are not up for discussion

Each of these is now enforced by a test, so you will meet it on the commit that breaks it rather than in review. The test names the rule and the reason.

**1. No manual controls.** No button, endpoint, or command that adds a ticker, starts an analysis, or places a trade. The app had twenty-three Discord commands and every one was removed on 2026-09-01.

A control that lets a person nudge the book puts a second decision-maker in the record. Afterwards nothing can tell which one produced a result, and a record with two authors is not evidence about either. **This is the experiment, not a preference.** If something needs correcting, write down what and why in `JOURNEY.md`, then correct it by hand.

There are exactly two write endpoints and both decide nothing. See `backend/tests/test_no_manual_controls.py`.

**2. No real money, ever.** Every order goes to Webull's sandbox. Four checks stand in front of each one and none may be relaxed — least of all on the reasoning that the agent behaves as if the stakes were real anyway. **The prompt may lie to the model; the code must never lie to itself.** See `backend/tests/test_every_order_passes_the_sandbox_guard.py`.

**3. Write the journal entry before the behaviour change, not after.** Behaviour here is mostly prompt, so a month of runs across three undocumented prompt revisions is three experiments with no way to tell them apart. A reason reconstructed two weeks later is a story about what you would like to have been thinking.

**4. Never resize an order.** Python refuses what cannot be executed as stated and never shrinks it to fit. Shrinking turns the agent's decision into a different one, and the record then describes a strategy nobody chose.

## Where a change gets written down

Three files, three questions. **One question decides between the first two: does this change make two periods of the experiment non-comparable?**

| File | Holds | When |
|---|---|---|
| [JOURNEY.md](https://github.com/nandyalu/ten-acre/blob/main/JOURNEY.md) | Changes to the agent | Any of the three tests below |
| [docs/changelog.md](https://github.com/nandyalu/ten-acre/blob/main/docs/changelog.md) | Everything else about the app | None of them |
| [CLAUDE.md](https://github.com/nandyalu/ten-acre/blob/main/CLAUDE.md) | What the rules are *now* | Reasoning a future edit must not undo |

Any one of these is enough for `JOURNEY.md`:

- **Behaviour** — it changes what the agent is shown, what it may ask for, or what Python refuses.
- **Evidence** — it changes what the record contains or means. Telemetry counts: a field that is null before a date is what trips up whoever reads the data later.
- **Incident** — the agent's behaviour changed without anyone intending it, so real days are contaminated. A silent bug feels like a fix and is not.

Everything else — setup, deployment, guards, infrastructure, site copy, docs, dependencies — is a changelog line.

**Keep entries to a sentence or two: what changed, and why.** Both files. If the agent's own tools changed, add a line to `backend/agent_changes.json` as well; that is the one-line version aimed at the agent, and it wakes the agent on the next restart.

## Running it

```sh
git clone --recurse-submodules https://github.com/nandyalu/ten-acre
cd ten-acre
uv sync --extra dev

uv run pytest backend/tests -q          # backend
cd frontend && npm install
npx ng test --watch=false               # frontend
npx ng build                            # template type-checking; writes backend/web
uvx zensical build                      # the documentation site; writes backend/site
```

`--recurse-submodules` matters: `TradingAgents/` is a real submodule and the app does not start without it.

Both build outputs land inside `backend/`, where the app serves them and where a release wheel carries them. Git ignores both folders.

To run the whole thing, see [docs/deploying.md](https://github.com/nandyalu/ten-acre/blob/main/docs/deploying.md). You need Webull sandbox credentials and a model endpoint; both are free, and the app's own `/setup` page tells you what is missing.

## Before you open a pull request

1. **All four commands above pass.** The frontend suite should report **zero** unhandled errors — it reported eight for weeks and they were hiding a real bug.
2. **The change is recorded** in the right file, per the table above.
3. **Nothing you changed left a stale description behind.** Run the `stale-check` skill in `.claude/skills/`, or work through it by hand. Roughly forty wrong statements were found in one day of sweeps: a deleted button still mentioned in the site copy, a schedule removed two days earlier still in the README, a variable name the app never reads printed by the setup page. None of them failed a test.
4. **Say what you measured.** "Faster" and "cleaner" are not results. If you changed something with a number attached, give the number and how you got it.

Small, single-purpose pull requests. If a change needs a `JOURNEY.md` entry, it is its own pull request.

## What is unlikely to be merged

- Anything that adds a manual control, however convenient.
- Anything that relaxes a guard.
- A position-size cap, or a similar limit on what the agent may decide. **Deliberately absent, not forgotten** — the agent has put the whole book into one name. A cap changes what the agent may decide rather than correcting its arithmetic, so it needs its own reasoning and its own journal entry, not a quiet fix.
- A rewrite of the prompt's "padding". Three rules in it read as filler and each was added after the model got that exact thing wrong with real orders.

## What is genuinely wanted

- **Replay** — re-run a past decision pass against a changed prompt, so a prompt change can be told apart from a market change. Behaviour here is mostly prompt, which makes this the missing measurement.
- **A backtester** — grade the strategy over history rather than only forward.
- **A watchlist ageing rule** — nothing currently drops a name the agent has stopped holding and stopped asking about.
- Bug reports with the log line attached. `docker logs`, or `logs/ten-acre.log` in the data directory.
- Anyone who runs their own copy: what broke, and which document lied to you.

## Licence, and what happens to your code

**This project is under the [PolyForm Noncommercial License 1.0.0](https://github.com/nandyalu/ten-acre/blob/main/LICENSE).** That is *source-available*, not open source: you may read it, run it, modify it and share it for any noncommercial purpose, and you may not sell it or use it in a commercial product. Read the licence rather than this summary.

By opening a pull request you agree to both of the following.

**1. You certify the [Developer Certificate of Origin](https://developercertificate.org/)** — in short, that you wrote the contribution or otherwise have the right to submit it under this project's licence. Certify it by signing off each commit:

```sh
git commit -s -m "..."
```

That appends `Signed-off-by: Your Name <your@email>` using your `git config user.name` and `user.email`. Use a real name and a working address.

**2. You grant the maintainer the right to release your contribution under any licence**, including a commercial one, in addition to PolyForm Noncommercial. You keep the copyright in what you wrote; this is a licence grant, not an assignment, and nothing here stops you using your own work elsewhere.

**Why the second one is asked for.** PolyForm Noncommercial keeps commercial rights with the author. Without this grant, contributed code could never be included in a commercial release without tracking down every contributor and asking — which in practice means either the contribution is never merged, or the option quietly disappears. Saying so up front is fairer than discovering it later.

If either term does not work for you, open an issue and say so before writing the code. A contribution nobody can merge helps neither of us.

*This section is a plain-language description of the terms, not legal advice. If it matters to you, read the licence and the DCO yourself, or ask someone qualified.*

## Contributing with an AI assistant

**Most of this codebase was written by one**, and there is a separate document about what that means and what it costs: [AI_POLICY.md](https://github.com/nandyalu/ten-acre/blob/main/AI_POLICY.md). Read it before you point a coding agent at this repo. It is not a prohibition — it is a list of the specific ways AI-authored changes have gone wrong here, in this repo, with dates.
