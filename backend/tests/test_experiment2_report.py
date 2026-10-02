"""Experiment 2's success test, on synthetic books."""
import datetime
import random

import pytest

from backend.scripts import experiment2_report as r

D = [datetime.date(2026, 10, 12) + datetime.timedelta(days=i) for i in range(5)]
CLOSES = {
    "AAA": dict(zip(D, [10.0, 11.0, 12.0, 12.0, 12.0])),
    "SPY": dict(zip(D, [100.0, 101.0, 102.0, 102.0, 102.0])),
}


def closes(ticker):
    return CLOSES.get(ticker, {})


def test_equity_counts_cash_holdings_and_research():
    trades = [r.Trade("AAA", "buy", 100, 10.0, D[0]), r.Trade("AAA", "sell", 100, 12.0, D[2])]
    run = r.series(trades, [(D[1], 5.0)], closes, D, 10_000)
    assert run.equity == [10_000, 10_095, 10_195, 10_195, 10_195]
    assert run.invested[:3] == [1_000, 1_100, 0]
    assert run.held[0] == frozenset({"AAA"}) and run.held[2] == frozenset()


def test_round_trips_are_first_in_first_out():
    trades = [
        r.Trade("AAA", "buy", 10, 10.0, D[0]),
        r.Trade("AAA", "buy", 10, 11.0, D[1]),
        r.Trade("AAA", "sell", 15, 12.0, D[3]),
    ]
    assert r.round_trips(trades, D) == [(3, 100.0), (2, 55.0)]


def test_the_primary_test_passes_only_above_the_95th_percentile():
    rng = random.Random(0)
    randoms = [i / 1000 for i in range(-50, 51)]
    assert r.primary([0.2, 0.2], randoms, rng, resamples=2000)["passes"]
    assert not r.primary([0.0, 0.0], randoms, rng, resamples=2000)["passes"]


def test_exposure_matched_spy_scales_spy_by_the_invested_share():
    run = r.Series(D[:2], [10_000, 10_000], [5_000, 5_000], [frozenset(), frozenset()])
    assert r.exposure_matched_spy(run, CLOSES["SPY"]) == pytest.approx(0.005)


def test_drawdown_and_overlap():
    assert r.max_drawdown([100, 120, 90, 130]) == pytest.approx(-0.25)
    a = r.Series(D[:2], [1, 1], [0, 0], [frozenset({"X", "Y"}), frozenset()])
    b = r.Series(D[:2], [1, 1], [0, 0], [frozenset({"X"}), frozenset()])
    assert r.overlap([a, b]) == pytest.approx(0.5)


def test_the_verdict_follows_the_written_rule():
    start = datetime.date(2026, 10, 12)
    early = start + datetime.timedelta(days=30)
    late = start + datetime.timedelta(days=200)
    assert r.verdict(start, early, 500, {"passes": True}).startswith("not yet")
    assert r.verdict(start, late, 99, {"passes": True}).startswith("inconclusive")
    assert r.verdict(start, late, 100, {"passes": False}) == "fail"
    assert r.verdict(start, late, 100, {"passes": True}) == "pass"


def test_a_random_book_draws_from_the_screens_and_never_spends_more_than_it_has():
    universe = {D[0]: {"AAA"}}
    ret = r.random_book(random.Random(1), universe, D, closes, n_buys=3, holds=[2], fractions=[0.5], budget=10_000)
    # Every buy is AAA at 10 or 11 and the price ends at 12, so the book
    # cannot lose, and cash limits it to the budget.
    assert 0 <= ret <= 0.2


def test_the_days_a_book_fetched_its_own_data_are_reported(tmp_path):
    import sqlite3

    path = tmp_path / "book.db"
    con = sqlite3.connect(path)
    con.execute("create table marketfetch (day date, kind text, source text, count int)")
    con.executemany("insert into marketfetch values (?, ?, ?, ?)", [
        ("2026-10-12", "quote", "market", 40),
        ("2026-10-12", "quote", "own", 2),
        ("2026-10-12", "daily", "own", 1),
        ("2026-10-09", "quote", "own", 5),  # before the start
    ])
    con.commit()
    assert r.read_own_fetches(str(path), datetime.date(2026, 10, 12)) == {"2026-10-12": 3}
    sqlite3.connect(tmp_path / "old.db").execute("create table other (x int)")
    assert r.read_own_fetches(str(tmp_path / "old.db"), datetime.date(2026, 10, 12)) == {}
