---
name: graft
description: This repo is indexed by graft/. For ANY task here, whether
  understanding how something works, finding where code lives, tracing what
  calls a symbol or what a change breaks, or scoping an edit, get your context
  from graft before grepping or reading source files.
---

# graft

**The SessionStart hook already lists the six tools and when to use each, and the MCP server repeats them.** This file holds only what neither of those says. Do not copy the tool list back in: a third copy of it goes stale first.

## The graph refreshes itself

Every retrieval tool rebuilds the graph before it answers, so a result always describes the code as it is now, uncommitted edits included. **Never run `graft build` after an edit.** `build` is for the LLM layer (`--deep` adds a concept map; skip it unless asked) and `check` fails a stale `graft/` for CI.

**The markdown under `graft/` is the one thing that lags.** The cards are a projection, rebuilt at the end of a turn and not on each query, so treat a card's spans as stale for any file you edited this turn. The tools never lag. `graft/INDEX.md` indexes the cards for a plain `grep`, but the tools are faster and exhaustive where it matters.

## When a result looks wrong

- **A path graft names is not on disk:** the index is ahead of the checkout, after a branch switch or an unpulled move. Do not read the missing file. `graft grep` the symbol to find where it lives now, or run `graft build`.
- **A grep returns nothing:** the pattern is too specific. Drop the receiver and the signature, keep the bare name, and run `graft grep` again. Do not fall back to `grep -rn`, which is slower and unranked. Raw grep is only for what graft does not index: docs, configs, and files created this turn.
- **A span ends with "+N more lines":** open that file at that exact range, and nothing wider.
- **A card lacks a detail:** ask a more specific question first. Read source only at the exact `file:line`, never a whole file to rebuild what graft already gives.

## Spend the fewest calls

A card's `covers:` list carries an authoritative `file:line` for every symbol it names, generated from source. Cite straight from it and do not re-open the file to check it. Trust the answer and act on it; reach for a second tool only on weak hits, a truncated span, or a need to be exhaustive.

## The savings line

Each retrieval tool opens its output with `[graft] tokens saved ≈ N`. Close any turn that used graft with one line summing them, and add the dollar figure when the lines carry one: `🌱 graft saved ~12,400 tokens (~$0.04) this turn, 3 calls`. A call with no such line saved nothing — leave it out. The statusline carries the session total.
