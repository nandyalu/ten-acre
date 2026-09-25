/**
 * Every term the site uses that a reader would not already know.
 *
 * **One definition, in one place, used everywhere.** Two explanations of
 * "refused" on two pages would reintroduce exactly the ambiguity the tooltip
 * exists to remove — so nothing defines a term inline in a template.
 *
 * The rule for what belongs here: a word a careful reader could not work out
 * from context, or one that means something narrower here than in ordinary
 * use. "Refused" is the clearest case of the second kind — everyone knows the
 * word, and almost nobody would guess it means Python declined the order
 * before it was ever sent.
 */
export interface Term {
  /** The key used in markup, and the anchor on the glossary page. */
  id: string;
  /** As it appears in a sentence. */
  label: string;
  /** One or two sentences. A tooltip nobody finishes reading is not a
   * tooltip — anything longer belongs on the glossary page. */
  short: string;
  /** The fuller entry, shown only on the glossary page. Optional: most terms
   * do not need one. */
  long?: string;
}

export const TERMS: Term[] = [
  {
    id: 'refused',
    label: 'refused',
    short:
      'Python declined the order before it was sent. The agent asked for something it could not do — more cash than it holds, more shares than it owns.',
    long: 'The agent decides what and how much; Python refuses what cannot be executed as stated, and never resizes. Shrinking an order would quietly turn the agent’s decision into a different one, and the record would then describe a strategy nobody chose. A refusal is fed back once and the agent is asked again, which is how it learns it may sell to fund a buy.',
  },
  {
    id: 'broker-said-no',
    label: 'broker said no',
    short:
      'The order was formed correctly and the broker would not take it — unsettled cash, a closed session, a symbol it will not trade.',
    long: 'Different from a refusal, and the difference matters. A refusal says the agent’s own arithmetic was wrong. This says the arithmetic was right and the world declined anyway. The agent is shown these on its next few passes so it does not simply propose the same thing again.',
  },
  {
    id: 'research-failed',
    label: 'not analysed',
    short:
      'The agent paid to have this looked at, and the analysis did not finish — no broker was involved.',
    long: 'Different from a refusal or "broker said no": nothing about an order was wrong, because this was never an order. The model service did not answer, or the day’s request limit was reached, before the analysis could produce a verdict. Any row already on record for this ticker is from an earlier analysis, not this attempt, and a retry is never charged twice for the one that failed.',
  },
  {
    id: 'vs-spy',
    label: 'vs SPY',
    short: 'Whether a call beat the S&P 500 over its own window. The number that actually counts.',
    long: 'An absolute win rate of 70% in a rising market can be worse than useless — holding the index and doing nothing would have beaten it. A Buy passes here only if the stock beat SPY; a Sell passes only if it lagged, because selling something that then underperformed the market was the right call.',
  },
  {
    id: 'alpha',
    label: 'alpha',
    short:
      'How much a call beat or missed SPY by, over the same window. Positive means it added something the index did not.',
  },
  {
    id: 'maturing',
    label: 'maturing',
    short:
      'Not judged yet. A call is graded when its horizon arrives — 14 days for a swing call, 30 for a position call.',
  },
  {
    id: 'graded',
    label: 'graded',
    short:
      'Judged three ways once its horizon arrived: against reality, against SPY, and against its own price target.',
  },
  {
    id: 'research-charge',
    label: 'research charge',
    // Deliberately states no figure. These strings are static data rather than
    // a template, and the charge is a setting — a deployment that changed it,
    // or set it to zero, would have this tooltip contradicting the pages
    // around it, which name the real amount.
    short:
      'What the agent pays to have one ticker analysed, whenever it decides a fresh look is worth ordering. It comes out of the same money it trades with.',
    long: 'The charge is the point of letting the agent choose what to study. Free research is just a longer watchlist, and an agent that pays nothing for being wrong about what was worth studying learns nothing from being wrong. Nothing on the watchlist is analysed on a schedule; the agent orders a look, holdings included, whenever it judges one worth the price.',
  },
  {
    id: 'untrack',
    label: 'untrack',
    short: 'The agent dropping a name it no longer wants to track. It frees a watchlist slot.',
    long: 'It cannot untrack something it holds. Nothing is analysed automatically, so untracking a position would take away the only way left to ever research it again — it has to sell first.',
  },
  {
    id: 'unguarded',
    label: 'unguarded',
    short:
      'The agent holds shares and nothing is resting at the broker to close them. The money is at risk and the exit everyone assumes is there is not.',
    long: 'A buy normally goes out as a bracket — the entry with a stop and a target attached — so the shares are never held bare. Webull refuses a bracket while cash is unsettled, which happens routinely when the agent sells to fund a buy, and the fallback that arms the exits separately can fail. This is the one thing on the site a person is asked to fix.',
  },
  {
    id: 'bracket',
    label: 'bracket',
    short:
      'A buy sent together with its stop and its target, as one order. The broker activates the exits the moment the entry fills.',
  },
  {
    id: 'resting-exit',
    label: 'resting exit',
    short:
      'A stop or a take-profit sitting at the broker, waiting. It executes whether or not this app is running.',
    long: 'Different from the levels a signal proposes. A signal’s stop is what the analysis suggested; a resting exit is what will actually happen. The two disagree often — a discarded level, a volatility-derived fallback, a bracket the broker refused.',
  },
  {
    id: 'r-multiple',
    label: 'R-multiple',
    short:
      'Reward measured in units of what is risked. A 2R target means the gain, if it works, is twice the loss if the stop is hit.',
  },
  {
    id: 'conviction-floor',
    label: 'conviction floor',
    short:
      'The minimum quality a signal must show before the agent may open a position on it. Zero by default, which switches it off.',
  },
  {
    id: 'horizon',
    label: 'horizon',
    short:
      'How far ahead a call is meant to work, and therefore when it gets graded. Swing is 1 to 2 weeks; position is a multi-month hold.',
  },
  {
    id: 'regime',
    label: 'regime',
    short:
      'A one-line reading of the market: VIX, the S&P against its 200-day average, and the yield curve. The agent sees this same sentence first in every prompt.',
  },
  {
    id: 'mechanical-follower',
    label: 'mechanical signal-follower',
    // No slot count and no start date here, for the same reason the research
    // charge names no figure: both are computed in the backend, and a static
    // string that named them would drift from the table beside it.
    short:
      'A rule with no model in it: buy on a Buy signal whenever one of its five slots is free, hold until a Sell signal or the call’s own maturity date, whichever comes first. It is what the agent has to beat.',
    long: 'It holds at most five names at a time, each one an equal slot of the money it has now — so a rule that has doubled its money puts twice as much into its next position, and nothing sits idle while there are signals to act on. Shares are fractional, which is what makes the five weights exactly rather than roughly equal. A signal arriving with all five slots taken is missed, not queued, the way a fully invested account misses one, and a ticker already held is skipped. An open position counts as one slot rather than what it is worth today: a gain is redeployed once the position closes and the money is really there. It starts from the agent’s first trade with the same budget, reads the same signals in date order, and pays for the same research. An entry is priced at the price the analysis itself saw, and a maturity exit at the price on the day the call was graded, so the rule follows the signals without hindsight. The maturity exit matters because nothing is analysed on a schedule: the agent chooses what gets a fresh look, and a name it stops revisiting would otherwise never produce a Sell for the rule to act on. It can only follow signals the agent paid for, so it measures the agent’s judgement and not its choice of what to research — SPY is the baseline that owes nothing to either. If it wins, the judgement added nothing.',
  },
  {
    id: 'decision-pass',
    label: 'decision pass',
    short:
      'The moment the agent reads its book and answers with orders. Not a fixed time — the agent names its own next one, so this happens whenever it asked to be woken.',
    long: 'Naming no time falls back to the following open, and a final pass always runs five minutes before the close whatever the agent asked for, so no position goes into the night unreviewed. Before 2026-09-05 this ran once a day at a fixed clock time; the agent now owns its own schedule entirely.',
  },
  {
    id: 'evening-review',
    label: 'evening review',
    short:
      'Once a trading day, after the close, the agent reads every decision pass since its last review and writes two things: notes to whoever maintains it, and a revision of its own memory. It cannot trade, research or move its alarm from here.',
    long: 'Most days it reports nothing, and that is the expected answer. A note names the pass where the gap was felt: a tool it lacked, information it could not see, a rule that contradicted another. It may also replace the note its last pass left for the next one. Nothing acts on a note automatically, and the review is shown inside the day it ran on, above that day’s passes.',
  },
];

export const TERMS_BY_ID = new Map(TERMS.map((t) => [t.id, t]));
