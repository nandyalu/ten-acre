/**
 * The analyst's rationale, split into the sections it is actually written in.
 *
 * It is Markdown — `**Rating**: Overweight`, then a blank line, then
 * `**Executive Summary**: …` — and it was being printed raw into a paragraph.
 * So a reader saw the asterisks, lost the section breaks, and read one
 * unbroken blob in a 680px measure beside analyst reports running to 1198px.
 * That is what made it look wrapped when the reports did not.
 *
 * **Parsed rather than rendered as Markdown.** The shape is fixed and known:
 * across 44 stored rationales from two different models, every one had
 * Rating, Executive Summary, Investment Thesis and Time Horizon, and 38 had
 * Price Target. Pulling five known labels out is smaller and more predictable
 * than adding a Markdown renderer for one field, and it degrades safely — an
 * unrecognised shape comes back as a single untitled section rather than
 * disappearing.
 */
export interface RationaleSection {
  /** "Rating", "Executive Summary", … or "" when the text had no labels. */
  label: string;
  text: string;
}

/** `**Label**: body`, where the body may run to the next label or the end. */
const LABEL = /^\*\*([^*]+)\*\*:\s*/;

/** Bold inside a body, which we do not render as bold — the asterisks were
 * showing literally, and stripping them beats printing them. */
const INLINE_BOLD = /\*\*([^*]+)\*\*/g;

export function parseRationale(raw: string | null | undefined): RationaleSection[] {
  const text = (raw ?? '').trim();
  if (!text) return [];

  const sections: RationaleSection[] = [];
  for (const block of text.split(/\n\s*\n/)) {
    const chunk = block.trim();
    if (!chunk) continue;
    const match = chunk.match(LABEL);
    if (match) {
      sections.push({
        label: match[1].trim(),
        text: chunk.slice(match[0].length).replace(INLINE_BOLD, '$1').trim(),
      });
    } else if (sections.length) {
      // A paragraph continuing the section above it, not a new one.
      sections[sections.length - 1].text += '\n\n' + chunk.replace(INLINE_BOLD, '$1');
    } else {
      sections.push({ label: '', text: chunk.replace(INLINE_BOLD, '$1') });
    }
  }
  return sections;
}

/** Why an analysis ran, in plain words.
 *
 * A signal produced because the stock just moved sharply is the analyst
 * reacting to a move the price already holds. A scheduled one is not
 * reacting to anything. Those deserve different weight, and the agent is
 * shown the same distinction in its own prompt.
 */
export function whyItRan(trigger: string): string {
  return (
    {
      sweep: 'The normal morning run over the whole watchlist',
      commissioned: 'The agent paid for it and asked to see it the same day',
      move: 'The stock moved unusually, so this reacts to a move already in the price',
      earnings: 'The company reports earnings soon',
      manual: 'Run by hand, outside the schedule',
    }[trigger] ?? trigger
  );
}
