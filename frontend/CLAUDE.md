# frontend — Claude Code context

**This is the public site for Ten Acre**, an experiment in which one AI agent trades a simulated account with no human help. It is a publication, not a tool: nothing here operates anything, and no control on it can change what the agent does. See `../PLAN.md` for the design plan and `../CLAUDE.md` for the experiment itself.

Angular 22 (standalone components, signals, zoneless), vitest for tests, `lightweight-charts` for both charts. No UI framework and no CSS preprocessor. Commands run from `frontend/`: `npx ng build` (full template type-check), `npx ng test`, `npx prettier --write "src/**/*.{ts,html,css}"`.

**The database holds the start date, not the site.** The date is stamped when the agent is first switched on, and this deployment's says 2026-09-10. `src/app/shared/experiment.ts` holds 2026-09-02 only as the fallback for a site with no API.

## Who reads it

Two audiences, and the failure mode is averaging them into something that serves neither.

- **A general reader** interested in AI and trading. Wants the story and whether it is working.
- **A technical reader** interested in experiments and LLM behaviour. Wants the method, the limits, and the raw data.

**The answer is progressive depth.** Every page opens with the plain-language answer in one bold phrase, and the evidence sits below it. A general reader stops at the top; a technical reader keeps scrolling.

## The stylesheet

Three files, imported in order by `src/styles.css`:

| File | What is in it |
|---|---|
| `src/fonts.css` | The `@font-face` rules. Generated; do not hand-edit |
| `src/theme.css` | Design tokens, the type scale, `.money`, motion and focus |
| `src/styles.css` | Element defaults, the shell, page primitives, and per-page blocks |

**Feature templates carry no styling of their own.** They compose the classes in the sheet. A component gets a `styles:` block only when the style depends on something the global sheet cannot see — the chart components size their own canvas that way, and nothing else should need it.

**Colours come from the tokens, never as literals.** The exception is the chart components, which draw to a canvas and cannot inherit a CSS variable; they read tokens through `shared/chart-theme.ts` at creation and again on every theme change. A new chart must do the same or its axis labels stay the wrong colour until reload.

**Two themes, always.** The dark palette is declared twice — once under `prefers-color-scheme`, once under `:root[data-theme='dark']`. CSS cannot share one block between a media query and an attribute selector, so a new token has to be added in both. `index.html` sets `data-theme` before the first paint; do not move that into Angular or dark readers get a white flash on every load.

## Rules that are not style preferences

**The chart palette was computed, not chosen.** Every categorical set passed six checks — lightness band, chroma floor, colourblind separation, normal-vision floor, contrast. A hand-picked "restrained" set failed three of them. Re-validate before changing any of them:

```sh
node scripts/validate_palette.js "<hex,hex,...>" --mode light --surface "#faf9f6"
```

**Colour is never the only difference between a gain and a loss.** The validator puts this red and green at ΔE 6.7 for the two common forms of colourblindness — roughly one man in twelve cannot separate them. So:

- `.money--pos` / `.money--neg` add an arrow through generated content, at the class rather than the call site.
- `.pass` / `.fail` are only correct where the value already carries a sign or a word.

**A measure is for long-form prose, not for anything that merely happens to be text.** `.prose` and `.subtitle` get one. A definition inside a card is already constrained by the card, and capping it again wraps short lines while half the card sits empty.

**Nothing on this site may nudge the book.** No control that adds a ticker, starts an analysis or places a trade. That rule is the experiment, not a design choice.

## Layout

A masthead over the reading column — not a sidebar, which is what a tool looks like. It starts at 145px and collapses to 63px on scroll.

Two breakpoints:

- **≥52rem** — the top nav is visible.
- **<52rem** — the nav is replaced by a drawer that opens *below* the masthead rather than sliding over the page. Every route must stay reachable from it; `app.spec.ts` asserts it.

**Size a logo in CSS, never with `transform: scale()`.** A scaled element keeps its original layout box, which is why the masthead could not collapse past the mark's full height until this was fixed.

**A new table needs `class="data-table data-table--stack"`, a `data-label` on every `<td>`, and a `.table-wrap` around it.** Below 640px the rows become labelled blocks built from those values; a cell without one renders as a value with no name.

## Terms

Every term a reader might not know is defined **once**, in `shared/glossary/terms.ts`, and used through `<app-term key="..." />`. Nothing defines a term inline in a template — two explanations of "refused" on two pages reintroduce the ambiguity the tooltip exists to remove.

**A tooltip explains a word. It never carries a fact you would otherwise miss.** Everything in one is also on `/glossary`, so a phone reader who never finds the tap target loses nothing.

## The pages

| Route | The question it answers |
|---|---|
| `/` | What is this, and how is it going? |
| `/book` | What did it do with the money? |
| `/decisions` | Why did it do that? |
| `/research` | What did it study, and was it right? |
| `/scorecard` | Is it any good? |
| `/journal` | What has been happening? |
| `/idea` | Why does this exist? |
| `/method` | How is it run, and what can it not tell you? |
| `/glossary` | What does that word mean? |

**Every state is designed, not just the happy one.** Loading uses `.skeleton` at the shape of the content. Empty uses `.empty` and says both what would appear and when — a fresh deployment is empty everywhere, and that is a developer's first impression. There is an API-down state too.

## Before committing

```sh
npx ng build && npx ng test --watch=false && npx prettier --write "src/**/*.{ts,html,css}"
```

Then sweep the pages: every route × both themes × phone, tablet and desktop, checking for horizontal overflow and console errors. A masthead bug that pushed 23px off the right of *every* page on a phone was invisible until that sweep ran.
