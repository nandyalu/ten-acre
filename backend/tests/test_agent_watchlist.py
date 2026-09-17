"""The agent choosing what to stop watching, and the cap that forces the choice.

Research adds to the watchlist permanently, so without an untrack the list is a
ratchet: it only ever grows, and the morning sweep grows with it until it runs
past the open. The cap makes the limit visible and the untrack action makes it
the agent's decision rather than a wall it hits.

Python's job here is the same as everywhere else: refuse what must not happen,
never reshape what was asked. The one thing it refuses outright is untracking a
held position, because a holding nobody analyses is a holding with nothing
watching for its exit.

Pure — no LLM, no broker.
"""
import pytest

from backend.services import agent, agent_book


class Candidate:
    def __init__(self, ticker, price=100.0, volume=5_000_000, change_pct=1.0, source="most active"):
        self.ticker, self.price, self.volume, self.change_pct = ticker, price, volume, change_pct
        self.source = source
        self.name = f"{ticker} Inc"

    @property
    def volume_m(self):
        return self.volume / 1_000_000


def _book(cash=1000.0, holdings=None):
    return agent_book.Book(
        budget=10_000.0, cash=cash, realized_pnl=0.0,
        holdings=[agent_book.Holding(ticker=t, quantity=q, avg_cost=c)
                  for t, q, c in (holdings or [])],
    )


@pytest.fixture
def watching(monkeypatch):
    """A watchlist of three, a cap of four, and research priced at 5 cents.

    Also stands in for get_recent_signals (never analysed, by default) —
    without it the new per-ticker watchlist line would hit the real local
    database, and NVDA/GOOG below are real tickers that could have real rows
    in it. Tests that care about the last-analysed text override this
    themselves.
    """
    def _setup(tickers=("AAA", "BBB", "CCC"), cap=4):
        monkeypatch.setattr(agent.research, "get_price", lambda: 0.05)
        monkeypatch.setattr(agent.research, "is_charging", lambda: True)
        monkeypatch.setattr(agent.db, "get_watchlist", lambda: list(tickers))
        monkeypatch.setattr(agent.db, "get_recent_signals", lambda ticker, limit=1: [])
        monkeypatch.setattr(agent, "_max_watchlist", lambda: cap)
        return list(tickers)
    return _setup


# --- what the agent is shown ---------------------------------------------------


def test_the_prompt_lists_the_watchlist_and_its_cap(watching):
    watchlist = watching()

    prompt = agent.build_prompt(_book(), [], {}, watchlist=watchlist, max_watchlist=4)

    assert "You track 3 of at most 4 tickers" in prompt
    assert "AAA" in prompt and "BBB" in prompt and "CCC" in prompt


def test_held_and_watched_only_are_marked_apart(watching):
    """Only a watched-only ticker can be dropped. A single undifferentiated
    list invites orders Python then refuses, which wastes the retry."""
    watchlist = watching()

    prompt = agent.build_prompt(
        _book(holdings=[("AAA", 3, 10.0)]), [], {},
        watchlist=watchlist, max_watchlist=4,
    )

    assert "| AAA | held |" in prompt
    assert "| BBB | watched |" in prompt
    assert "| CCC | watched |" in prompt


def test_a_full_watchlist_says_so_rather_than_waiting_to_refuse(watching):
    watchlist = watching(tickers=("AAA", "BBB", "CCC", "DDD"), cap=4)

    prompt = agent.build_prompt(_book(), [], {}, watchlist=watchlist, max_watchlist=4)

    assert "That is the limit" in prompt


def test_the_rules_explain_untracking_as_a_swap(watching):
    """The identical wording had to be added for sells funding buys before the
    model worked out it could reorder. Do not assume it generalizes."""
    watching()

    prompt = agent.build_prompt(_book(), [], {}, watchlist=["AAA"], max_watchlist=4)

    assert 'side "untrack"' in prompt
    assert "list the untrack" in prompt and "research after it" in prompt
    assert "cannot untrack something you hold" in prompt


def test_no_watchlist_section_when_there_is_no_cap():
    """The live deployment does not run this feature. A rule about a limit that
    is not enforced is prompt tokens spent on a lie."""
    prompt = agent.build_prompt(_book(), [], {}, watchlist=["AAA"], max_watchlist=0)

    assert "untrack" not in prompt


def test_a_menu_renders_for_a_book_that_holds_something(watching):
    """Regression. The holdings loop used a local named `price`, which rebound
    the research-price parameter to a string, and the menu block below then
    formatted that string as a float. It needed a holding and a menu in the
    same prompt, so it survived every existing test and would have raised on
    the analyst's first pass after its first buy.
    """
    watching()
    book = _book(holdings=[("GOOG", 2, 343.66)])
    book.holdings[0].price = 344.0

    prompt = agent.build_prompt(
        book, [], {}, menu=[Candidate("NVDA")], price=0.05,
        watchlist=["GOOG", "NVDA"], max_watchlist=4,
    )

    assert "order costs $0.05 and runs inside this pass" in prompt
    assert "| $344.00 |" in prompt


def test_signals_do_not_rebind_the_research_price_either(watching):
    """The same shadowing existed in the signals loop, where it would have set
    the research price to a float and quietly printed the wrong number."""
    watching()

    class Signal:
        ticker, signal_date, decision = "NVDA", "2026-08-27", "Buy"
        entry_price = stop_loss = price_target = None
        win_probability = risk_reward = expected_value_r = None

    prompt = agent.build_prompt(
        _book(), [Signal()], {"NVDA": 180.0}, menu=[Candidate("AMD")],
        price=0.05, watchlist=["NVDA"], max_watchlist=4,
    )

    assert "order costs $0.05 and runs inside this pass" in prompt
    assert "| $180.00 |" in prompt


# --- what Python enforces ------------------------------------------------------


def test_untracking_a_watched_ticker_is_accepted(watching):
    watching()

    accepted, rejected = agent.screen(
        [{"ticker": "BBB", "side": "untrack"}], _book(), {}, None, None,
    )

    assert rejected == []
    assert accepted[0]["ticker"] == "BBB" and accepted[0]["side"] == "untrack"
    assert accepted[0]["quantity"] == 0


def test_a_held_ticker_cannot_be_untracked(watching):
    """The rule that must never be relaxed. Untracking a holding would leave
    no way to ever research it again, and nothing analyses it automatically
    to make up for that."""
    watching()

    accepted, rejected = agent.screen(
        [{"ticker": "AAA", "side": "untrack"}],
        _book(holdings=[("AAA", 3, 10.0)]), {}, None, None,
    )

    assert accepted == []
    assert rejected[0].side == "untrack"
    assert "sell it first" in rejected[0].why


def test_untracking_something_unwatched_is_refused(watching):
    watching()

    _, rejected = agent.screen(
        [{"ticker": "ZZZ", "side": "untrack"}], _book(), {}, None, None,
    )

    assert "nothing to stop watching" in rejected[0].why


def test_research_is_refused_when_the_watchlist_is_full(watching):
    watching(tickers=("AAA", "BBB", "CCC", "DDD"), cap=4)

    _, rejected = agent.screen(
        [{"ticker": "NEW", "side": "research"}], _book(), {}, None, {"NEW"},
    )

    assert "watchlist is full at 4" in rejected[0].why
    assert "untrack something first" in rejected[0].why


def test_an_untrack_frees_a_slot_for_a_research_listed_after_it(watching):
    """The point of the whole design. A full watchlist is a swap, not a wall,
    so the agent trades one name's coverage for another in the same pass."""
    watching(tickers=("AAA", "BBB", "CCC", "DDD"), cap=4)

    accepted, rejected = agent.screen(
        [{"ticker": "DDD", "side": "untrack"},
         {"ticker": "NEW", "side": "research"}],
        _book(), {}, None, {"NEW"},
    )

    assert rejected == []
    assert [o["side"] for o in accepted] == ["untrack", "research"]


def test_the_order_matters_the_same_way_a_sell_funding_a_buy_does(watching):
    """Research listed first sees the full watchlist and is refused. This is
    the behaviour the prompt warns about, and it must actually happen."""
    watching(tickers=("AAA", "BBB", "CCC", "DDD"), cap=4)

    accepted, rejected = agent.screen(
        [{"ticker": "NEW", "side": "research"},
         {"ticker": "DDD", "side": "untrack"}],
        _book(), {}, None, {"NEW"},
    )

    assert [o["side"] for o in accepted] == ["untrack"]
    assert rejected[0].ticker == "NEW" and "full at 4" in rejected[0].why


def test_two_researches_cannot_share_one_freed_slot(watching):
    """The running-watchlist equivalent of three buys that each fit the opening
    cash but not each other."""
    watching(tickers=("AAA", "BBB", "CCC", "DDD"), cap=4)

    accepted, rejected = agent.screen(
        [{"ticker": "DDD", "side": "untrack"},
         {"ticker": "ONE", "side": "research"},
         {"ticker": "TWO", "side": "research"}],
        _book(), {}, None, {"ONE", "TWO"},
    )

    assert [o["ticker"] for o in accepted] == ["DDD", "ONE"]
    assert rejected[0].ticker == "TWO" and "full at 4" in rejected[0].why


def test_researching_something_already_watched_does_not_need_a_free_slot(watching):
    """Since 2026-09-08 a ticker already on the list can be re-researched —
    nothing analyses it automatically any more. Refusing it for a full
    watchlist would be wrong too: re-researching does not grow the list."""
    watching(tickers=("AAA", "BBB", "CCC", "DDD"), cap=4)  # already at the cap

    accepted, rejected = agent.screen(
        [{"ticker": "AAA", "side": "research"}], _book(), {}, None, {"AAA"},
    )

    assert rejected == []
    assert accepted[0]["ticker"] == "AAA"


# --- what happens after it is accepted -----------------------------------------


def test_an_accepted_untrack_removes_it_and_is_recorded(watching, monkeypatch):
    watching()
    removed = []
    monkeypatch.setattr(agent.db, "remove_from_watchlist", removed.append)
    run = agent.AgentRun()

    agent._untrack({"ticker": "BBB", "side": "untrack"}, run)

    assert removed == ["BBB"]
    assert run.untracked == ["BBB"] and run.failed == []


def test_a_failed_removal_is_reported_rather_than_raising(watching, monkeypatch):
    """A pass that traded successfully must not die because a note could not
    be filed."""
    watching()

    def boom(_ticker):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(agent.db, "remove_from_watchlist", boom)
    run = agent.AgentRun()

    agent._untrack({"ticker": "BBB", "side": "untrack"}, run)

    assert run.untracked == [] and len(run.failed) == 1


def test_a_run_that_only_untracked_is_not_an_idle_run():
    """It changes what tomorrow's sweep costs, so reporting it as "no action"
    would hide a real decision."""
    run = agent.AgentRun(untracked=["BBB"])

    assert run.acted is True


def test_the_retry_says_how_to_fix_a_full_watchlist(watching):
    """The retry is the one chance to correct a refusal, and advice about cash
    does not help here. Observed on the first live probe: the model asked to
    research two names without untracking anything."""
    watching()
    refusal = agent_book.Rejection(
        ticker="NVDA", side="research", quantity=0,
        why="the watchlist is full at 4 — untrack something first",
    )

    prompt = agent.build_prompt(
        _book(), [], {}, rejected=[refusal], watchlist=["AAA"], max_watchlist=4,
    )

    assert "untrack something first and list the untrack before the research" in prompt


def test_a_cash_refusal_does_not_mention_the_watchlist(watching):
    """Every extra line is prompt tokens on a model that already reads 100k."""
    watching()
    refusal = agent_book.Rejection(
        ticker="NVDA", side="buy", quantity=2, why="costs more than the cash left",
    )

    prompt = agent.build_prompt(
        _book(), [], {}, rejected=[refusal], watchlist=["AAA"], max_watchlist=4,
    )

    assert "list the untrack before the research" not in prompt


def test_tracking_itself_is_now_free(watching):
    """Since 2026-09-08 nothing is analysed automatically — the per-morning
    aggregate cost this used to state ($0.05 x tracked count) no longer
    exists, because tracking a ticker no longer buys it a daily analysis.
    Only a "research" order costs anything, once, per use."""
    watching()

    prompt = agent.build_prompt(
        _book(), [], {}, watchlist=["AAA", "BBB", "CCC"], max_watchlist=12, price=0.05,
    )

    assert "paying $0.15 every morning" not in prompt
    assert "Nothing is analysed automatically" in prompt


def test_a_free_deployment_still_shows_the_watchlist(watching):
    """The live bot does not charge for research, but the tracked-ticker list
    with its prices is still useful information regardless of price."""
    watching()

    prompt = agent.build_prompt(
        _book(), [], {}, watchlist=["AAA", "BBB"], max_watchlist=12, price=0.0,
    )

    assert "costs $0.00 and runs inside this pass" in prompt
    assert "You track 2 of at most 12 tickers" in prompt


class _LastSignal:
    def __init__(self, signal_date, price_at_signal, decision):
        self.signal_date, self.price_at_signal, self.decision = signal_date, price_at_signal, decision


def test_a_never_analysed_ticker_says_so(watching):
    """The whole point of the watchlist rewrite: a ticker nothing has looked
    at yet must not silently look the same as one that has."""
    watching()

    prompt = agent.build_prompt(
        _book(), [], {"AAA": 50.0}, watchlist=["AAA"], max_watchlist=4,
    )

    assert "| AAA | watched | $50.00 | — | — | never | never | never | never |" in prompt


def test_a_previously_analysed_ticker_shows_its_staleness(watching, monkeypatch):
    """Enough to decide whether a fresh look is worth $0.05: what it last
    said, and how far the price has moved since."""
    watching()
    monkeypatch.setattr(
        agent.db, "get_recent_signals",
        lambda ticker, limit=1: [_LastSignal("2026-09-01", 340.00, "Overweight")],
    )

    prompt = agent.build_prompt(
        _book(), [], {"AAA": 368.36}, watchlist=["AAA"], max_watchlist=4,
    )

    line = next(l for l in prompt.splitlines() if l.startswith("| AAA |"))
    assert "| $368.36 |" in line
    assert "| 2026-09-01 | $340.00 |" in line
    assert "+8.3%" in line
    assert "Overweight" in line


def test_a_ticker_with_no_live_price_says_unavailable_not_unanalysed(watching, monkeypatch):
    """A delisted or rate-limited ticker must read as a data gap, not as
    something nobody has bothered to look at."""
    watching()
    monkeypatch.setattr(
        agent.db, "get_recent_signals",
        lambda ticker, limit=1: [_LastSignal("2026-09-01", 340.00, "Overweight")],
    )

    prompt = agent.build_prompt(_book(), [], {}, watchlist=["AAA"], max_watchlist=4)

    line = next(l for l in prompt.splitlines() if l.startswith("| AAA |"))
    assert "| unavailable |" in line
    # No live price means no percentage move can be computed.
    assert "%" not in line


# --- an empty balance ----------------------------------------------------------


def test_an_empty_balance_says_so_instead_of_quoting_itself_as_a_limit(watching):
    """The rules block opened with "must cost $-8.00 or less in total", which is
    not an instruction anybody can follow. The agent reached that balance on
    2026-08-28 and stayed there."""
    watching()

    prompt = agent.build_prompt(
        _book(cash=-8.0), [], {}, watchlist=["AAA"], max_watchlist=12, price=0.05,
    )

    assert "You have no money to spend. The balance is $-8.00." in prompt
    assert "must cost $-8.00 or less" not in prompt


def test_it_names_the_only_thing_that_raises_cash(watching):
    watching()

    prompt = agent.build_prompt(
        _book(cash=0.0), [], {}, watchlist=["AAA"], max_watchlist=12, price=0.05,
    )

    assert "Selling is the only thing that raises cash" in prompt


def test_a_zero_balance_no_longer_claims_an_automatic_charge(watching):
    """Both these lines were true only while the sweep existed: a book at
    zero no longer drifts down on its own, because nothing is charged without
    the agent asking for it. Claiming otherwise would tell it to expect a
    charge that never comes."""
    watching()

    prompt = agent.build_prompt(
        _book(cash=-8.0), [], {}, watchlist=["AAA"], max_watchlist=12, price=0.05,
    )

    assert "charged tomorrow whether" not in prompt
    assert "Untracking raises no cash and stops part of the charge" not in prompt
    assert "You have no money to spend. The balance is $-8.00." in prompt


def test_a_book_with_money_keeps_the_spending_limit(watching):
    """The normal case must not change. The limit is the one rule three live
    failures were fixed by wording carefully."""
    watching()

    prompt = agent.build_prompt(
        _book(cash=812.40), [], {}, watchlist=["AAA"], max_watchlist=12, price=0.05,
    )

    assert "must cost $812.40 or less in total" in prompt
    assert "no money to spend" not in prompt


def test_the_threshold_is_what_python_actually_refuses_at(watching):
    """Cash above the research price is not "no money": the agent can still
    commission an analysis, and screen() will accept it."""
    watching()

    prompt = agent.build_prompt(
        _book(cash=0.06), [], {}, watchlist=["AAA"], max_watchlist=12, price=0.05,
    )

    assert "must cost $0.06 or less" in prompt
    assert "no money to spend" not in prompt


# --- the affordable-share count ------------------------------------------------


class _Sig:
    ticker, signal_date, decision = "SMCI", "2026-09-01", "Buy"
    entry_price = 37.28
    stop_loss = 32.56
    price_target = 46.73
    win_probability = 65.0
    risk_reward = 2.0
    expected_value_r = 0.95


def _signal_line(cash: float, price: float = 36.51) -> str:
    prompt = agent.build_prompt(_book(cash=cash), [_Sig()], {"SMCI": price})
    # A table row since 2026-09-10, not a dashed sentence.
    return next(l for l in prompt.splitlines() if l.startswith("| SMCI |"))


def test_a_negative_balance_does_not_offer_negative_shares():
    """int(-8.00 // 36.51) is -1, not 0, and -1 is truthy — so a book at minus
    $8.00 was told "you can afford -1 share(s)" on every signal line."""
    line = _signal_line(cash=-8.0)

    assert "-1 share(s)" not in line
    assert "none, too dear" in line


def test_an_empty_balance_says_it_cannot_afford_any():
    assert "none, too dear" in _signal_line(cash=0.0)


def test_a_balance_below_one_share_says_the_same():
    """The boundary the old check got right and the negative case did not."""
    assert "none, too dear" in _signal_line(cash=20.0)


def test_a_balance_that_buys_shares_still_states_the_count():
    """The line exists because a model proposed $1,944 of buys against $1,000
    of cash. Fixing the negative case must not lose the count."""
    line = _signal_line(cash=100.0)

    assert "| 2 share(s) |" in line
