"""The concurrency footgun, pinned. It has shipped three times.

``for ticker in tickers: await run_analysis_and_record(ticker)`` looks
completely correct, passes review, and silently pins the whole batch to one
GPU: one LLM request is in flight at a time no matter how many backends the
pool has, so every extra card idles. On this hardware that is the difference
between about 43 minutes and about seven hours for fourteen tickers.

.claude/rules/gpu-pool.md records that this bug shipped in the daily sweep, the watchdog
triggers, and the earnings check — three separate times, by people who knew
about it. It is invisible in a diff and produces no error, only a slow day.

``analysis.run_analyses()`` dispatches with ``asyncio.gather`` and lets the
shared semaphore do the bounding, which is the only correct way to analyse
more than one ticker.
"""
import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ANALYSIS = BACKEND / "services" / "analysis.py"

# Analysing exactly one ticker. Awaiting one of these inside a loop is the bug.
SINGLE_TICKER = {"run_analysis_and_record", "propagate_ticker", "run_analysis_and_notify"}


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _awaited_single_ticker_calls(body: list[ast.stmt]) -> list[str]:
    found = []
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
                name = _called_name(node.value)
                if name in SINGLE_TICKER:
                    found.append(name)
    return found


def test_no_module_awaits_a_single_ticker_analysis_inside_a_loop():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(errors="ignore"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                continue
            for name in _awaited_single_ticker_calls(node.body):
                offenders.append(
                    f"{path.relative_to(BACKEND.parent)}:{node.lineno} awaits {name}() in a loop"
                )

    assert not offenders, (
        "An analysis is being awaited one ticker at a time:\n  "
        + "\n  ".join(offenders)
        + "\n\nThat holds exactly one LLM request in flight however many GPUs the "
        "pool has, so the extra cards idle and the batch takes N times as long. "
        "It produces no error — only a slow day — which is why it has shipped "
        "three times.\n\nUse analysis.run_analyses(tickers), which dispatches with "
        "asyncio.gather and lets the shared semaphore bound the concurrency."
    )


def test_run_analyses_still_dispatches_together():
    """The test above is only meaningful while the recommended alternative is
    actually concurrent. If run_analyses ever became a loop itself, every
    caller would inherit the bug and nothing would say so."""
    tree = ast.parse(ANALYSIS.read_text())
    func = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run_analyses"
    )

    gathers = [
        n for n in ast.walk(func)
        if isinstance(n, ast.Call) and _called_name(n) == "gather"
    ]
    assert gathers, (
        "run_analyses no longer dispatches with asyncio.gather. Every multi-ticker "
        "caller relies on it to keep more than one analysis in flight."
    )

    serial = [
        n.lineno for n in ast.walk(func)
        if isinstance(n, (ast.For, ast.AsyncFor)) and _awaited_single_ticker_calls(n.body)
    ]
    assert not serial, (
        f"run_analyses awaits an analysis inside a loop at line(s) {serial} — "
        "the very bug it exists to prevent."
    )
