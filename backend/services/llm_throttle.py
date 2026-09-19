"""Keep LLM calls inside whatever rate limit the vendor is enforcing.

The local GPU pool has no rate limit, so for most of this experiment's life
there was nothing to stay inside. A metered vendor is different: testing
Cerebras' free tier on 2026-09-09 returned a mix of 200s and 429s against both
its requests-per-minute and its tokens-per-minute ceilings, and the app had no
answer to either.

**Nothing here is guessed.** The first draft of this module invented an
exponential ladder — 5s, then 10, then 20 — and that would have been wrong in
a way that matters: the vendor asks for **58 seconds**, so six attempts on the
ladder would all have been spent inside the window the server was still
waiting out. Measured instead, on a deliberately tripped limit:

- Every **success** carries the full budget: ``x-ratelimit-remaining-requests-minute``
  and ``-tokens-minute`` (plus hour and day). So the app can know it is one
  call from the ceiling *before* it hits it.
- Every **429** carries ``retry-after`` in seconds, and a body naming the
  ceiling that was hit (``"Requests per minute limit exceeded"``,
  ``code: request_quota_exceeded``).

**A vendor that says none of this is left alone.** ollama returns no such
headers, so ``_remaining`` stays unknown and nothing ever waits — the local
path costs one dict lookup per call. That is the property to preserve in any
future edit: no delay may be introduced that a vendor did not ask for.

**One budget for the whole process.** The limit belongs to the account, not to
the run. Seven concurrent analyses share it, so a header read by one is a fact
about all seven. LangChain's own ``max_retries`` backs off per request, which
leaves seven analyses discovering the same ceiling seven times over while
keeping the endpoint saturated.

**A vendor that reports nothing can still be kept inside its limits
(2026-09-13).** Gemini sends no budget headers, and its 429 puts the wait in
the error body, not in ``retry-after``. Its free tier allows 15 requests a
minute and 500 a day, and one analysis makes about 20 calls in 70 seconds, so
waiting for refusals alone would collect them on every analysis. A deployment
states the limits in three variables:

- ``LLM_REQUESTS_PER_MINUTE`` and ``LLM_TOKENS_PER_MINUTE``: the app waits
  before a call that would pass either one.
- ``LLM_REQUESTS_PER_DAY``: the app refuses a call that would pass it, and the
  agent refuses research that cannot finish inside what is left.
  ``LLM_DAY_TIMEZONE`` says where the vendor's day starts; Google's starts at
  midnight Pacific time.

Unset, each one costs nothing. That keeps the rule above: this waits only where
a vendor or a person stated a limit.
"""
import collections
import datetime
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

log = logging.getLogger("ten-acre.llm_throttle")

# Attempts per call before the failure is handed back. Each wait is whatever
# the vendor asked for, so this is a count of refusals tolerated, not a ladder.
_ATTEMPTS = 4
# Used only when a 429 arrives with no retry-after to read.
_FALLBACK_WAIT_SECONDS = 30.0
# Below this many requests left in the current minute, wait for the window to
# roll rather than spend the last one and collect a 429. One, not zero: the
# header is read after the call that consumed it, and concurrent analyses are
# racing for the same budget.
_REQUEST_HEADROOM = 1
_WINDOW_SECONDS = 60.0

# One analysis makes about twenty model calls: 20.4 on average across 57
# gemini-3.5-flash-lite analyses, and at most 24 on gemma4-e4b-qat-128k. The
# margin is for a long analysis, so the daily check never starts one that
# cannot finish.
REQUESTS_PER_ANALYSIS = 25
_DEFAULT_DAY_TIMEZONE = "America/Los_Angeles"
_EASTERN = ZoneInfo("America/New_York")
# The day's count, kept in BotSetting as {"day", "count"} so that a restart
# does not start it again at zero.
_DAY_SETTING_KEY = "llm_requests_today"

# Gemini puts the wait in the error body: "'retryDelay': '41s'" in its JSON,
# or "retry_delay { seconds: 41 }" in the protobuf text form.
_RETRY_DELAY = re.compile(
    r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s|retry_delay\s*\{\s*seconds:\s*(\d+)"
)

_lock = threading.Lock()
# What the vendor last told us, or None where it told us nothing.
_remaining_requests: int | None = None
_window_started_at = 0.0
# Calls started in the last minute, oldest first, as [started_at, tokens].
# Empty unless a per-minute limit is stated.
_recent_calls: collections.deque = collections.deque()
# The vendor's current day, and the requests counted against it.
_day: str | None = None
_day_count = 0
# Variables already reported as unreadable, so a typo is logged once.
_warned: set[str] = set()


class DailyLimitReached(RuntimeError):
    """The day's stated request limit is spent.

    Raised before the call is sent, and never retried. The count does not start
    again for hours, and a wait that long inside a pass would hold the agent's
    lock all night.
    """


@dataclass(frozen=True)
class Limits:
    requests_per_minute: int | None
    tokens_per_minute: int | None
    requests_per_day: int | None
    day_timezone: str


def _positive_int(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        if name not in _warned:
            _warned.add(name)
            log.warning("%s=%r is not a whole number, so no limit is applied", name, raw)
        return None
    return value if value > 0 else None


def limits() -> Limits:
    """The limits this deployment stated. Read from the environment on each
    call, so a test can set them. Unset, empty, or zero means no limit."""
    zone = (os.environ.get("LLM_DAY_TIMEZONE") or "").strip() or _DEFAULT_DAY_TIMEZONE
    return Limits(
        requests_per_minute=_positive_int("LLM_REQUESTS_PER_MINUTE"),
        tokens_per_minute=_positive_int("LLM_TOKENS_PER_MINUTE"),
        requests_per_day=_positive_int("LLM_REQUESTS_PER_DAY"),
        day_timezone=zone,
    )


def rate_limited(exc: Exception) -> bool:
    """Whether the vendor refused this call for being too fast, rather than
    for anything asking differently would fix."""
    if getattr(exc, "status_code", None) == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


def retry_after(exc: Exception) -> float | None:
    """The wait the vendor asked for, in seconds, or None if it did not say.

    Authoritative and always preferred over any number this app would pick:
    Cerebras asks for 58 seconds on a requests-per-minute refusal, which no
    reasonable backoff ladder would have landed on. Gemini says it in the
    error body instead of a header.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        match = _RETRY_DELAY.search(str(exc))
        if match:
            value = match.group(1) or match.group(2)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def which_limit(exc: Exception) -> str:
    """Which ceiling the vendor says was hit, for the log — requests or tokens.

    Worth recording rather than flattening into "rate limited": a
    requests-per-minute refusal means slow down, a tokens-per-minute one means
    the prompts are too big for the pace, and those call for different fixes.
    """
    text = str(exc).lower()
    if "token" in text:
        return "tokens per minute"
    if "request" in text:
        return "requests per minute"
    return "an unnamed limit"


def _note_budget(headers) -> None:
    """Record what a successful response said is left in this minute."""
    global _remaining_requests, _window_started_at
    if not headers:
        return
    value = headers.get("x-ratelimit-remaining-requests-minute")
    if value is None:
        return  # a vendor that does not report, e.g. ollama — never throttled
    try:
        remaining = int(value)
    except (TypeError, ValueError):
        return
    with _lock:
        _remaining_requests = remaining
        if _window_started_at == 0.0 or remaining > (_remaining_requests or 0):
            _window_started_at = time.monotonic()


def _wait_for_the_window() -> None:
    """If the last response said the per-minute requests are nearly gone, wait
    for the window to roll instead of spending the last one on a refusal."""
    global _remaining_requests
    with _lock:
        if _remaining_requests is None or _remaining_requests > _REQUEST_HEADROOM:
            return
        elapsed = time.monotonic() - _window_started_at
        wait = max(0.0, _WINDOW_SECONDS - elapsed)
        # Assume the window rolled; the next response's header corrects us.
        _remaining_requests = None
    if wait > 0:
        log.info("Within one request of the per-minute ceiling — waiting %.0fs", wait)
        time.sleep(wait)


def current_budget() -> int | None:
    """Requests left in this minute as last reported, or None when the vendor
    reports nothing. Exported so a test and an operator can both read it."""
    return _remaining_requests


def _forget_header_budget() -> None:
    """After a refusal, the budget a header reported is stale. The per-minute
    window and the day's count stay, because those calls were really sent."""
    global _remaining_requests, _window_started_at
    with _lock:
        _remaining_requests = None
        _window_started_at = 0.0


def reset() -> None:
    """Forget what any vendor reported. For tests, and for a provider switch:
    a budget learned from one vendor says nothing about another's.

    The day's count is forgotten in memory only. The next call reads it back
    from the database, so a reset cannot give back requests already spent.
    """
    global _remaining_requests, _window_started_at, _day, _day_count
    with _lock:
        _remaining_requests = None
        _window_started_at = 0.0
        _recent_calls.clear()
        _day = None
        _day_count = 0


# --- the limits a deployment states ---------------------------------------------


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        if name not in _warned:
            _warned.add(name)
            log.warning("LLM_DAY_TIMEZONE=%r is not a time zone; using %s", name, _DEFAULT_DAY_TIMEZONE)
        return ZoneInfo(_DEFAULT_DAY_TIMEZONE)


def _now(zone: ZoneInfo) -> datetime.datetime:
    return datetime.datetime.now(zone)


def _load_day_count(day: str) -> int:
    """The count stored for ``day``, or 0 when the stored count is for another day."""
    try:
        from backend.database import db

        stored = json.loads(db.get_setting(_DAY_SETTING_KEY) or "{}")
    except Exception:
        log.warning("Could not read today's model request count", exc_info=True)
        return 0
    return int(stored.get("count") or 0) if stored.get("day") == day else 0


def _save_day_count(day: str, count: int) -> None:
    """Never raises. A count that could not be stored costs accuracy after a
    restart. A call lost to it would cost a pass."""
    try:
        from backend.database import db

        db.set_setting(_DAY_SETTING_KEY, json.dumps({"day": day, "count": count}))
    except Exception:
        log.warning("Could not store today's model request count", exc_info=True)


def _sync_day(lim: Limits) -> None:
    """Start the count again when the vendor's day has changed. Call with the lock held."""
    global _day, _day_count
    today = _now(_zone(lim.day_timezone)).date().isoformat()
    if _day != today:
        _day = today
        _day_count = _load_day_count(today)


def requests_left_today() -> int | None:
    """Requests left under the stated daily limit, or None when none is stated."""
    lim = limits()
    if lim.requests_per_day is None:
        return None
    with _lock:
        _sync_day(lim)
        return max(0, lim.requests_per_day - _day_count)


def describe_daily_shortfall(left: int) -> str:
    """Why research cannot start, in words the agent reads."""
    lim = limits()
    zone = _zone(lim.day_timezone)
    tomorrow = _now(zone).date() + datetime.timedelta(days=1)
    starts = datetime.datetime.combine(tomorrow, datetime.time(0, 0), tzinfo=zone).astimezone(_EASTERN)
    return (
        f"The model that runs you and the analysts allows {lim.requests_per_day} requests a day, "
        f"and {left} are left today. One analysis needs about {REQUESTS_PER_ANALYSIS}. "
        f"The count starts again on {starts.strftime('%A %-d %B at %-I:%M %p')} Eastern."
    )


def _estimate_tokens(kwargs: dict) -> int:
    """A rough count of a request's input tokens, from its characters.

    Used only to decide whether to wait. When the call returns, the vendor's
    own count replaces it. Four characters to a token is the usual rule for
    English. The text of a Gemini request object also contains field names, so
    this guess is high, which errs toward waiting.
    """
    body = kwargs.get("messages") or kwargs.get("contents") or ""
    return len(str(body)) // 4


def _tokens_used(result) -> int | None:
    """Input plus output tokens, as the vendor counted them. Counting both can
    only keep the app further under a token limit, whichever the vendor counts."""
    total = getattr(getattr(result, "usage", None), "total_tokens", None)
    if total is None:
        total = getattr(getattr(result, "usage_metadata", None), "total_token_count", None)
    try:
        return int(total) if total else None
    except (TypeError, ValueError):
        return None


def _minute_wait(lim: Limits, estimate: int) -> float:
    """Seconds until a call of ``estimate`` tokens fits the per-minute limits.
    Call with the lock held."""
    now = time.monotonic()
    while _recent_calls and now - _recent_calls[0][0] >= _WINDOW_SECONDS:
        _recent_calls.popleft()
    if not _recent_calls:
        # An empty minute always admits one call, even one larger than the
        # token limit. Otherwise that call would wait forever.
        return 0.0
    too_many = lim.requests_per_minute is not None and len(_recent_calls) >= lim.requests_per_minute
    too_big = (
        lim.tokens_per_minute is not None
        and sum(tokens for _, tokens in _recent_calls) + estimate > lim.tokens_per_minute
    )
    if not (too_many or too_big):
        return 0.0
    # When the oldest call leaves the window. The loop in _reserve asks again.
    return _WINDOW_SECONDS - (now - _recent_calls[0][0]) + 0.05


def _reserve(estimate: int):
    """Count one call against the stated limits, and wait first when a
    per-minute limit would be passed.

    Returns the entry to correct with the real token count, or None when no
    per-minute limit is stated. Raises DailyLimitReached at once, without a
    wait, when the day's requests are spent.
    """
    global _day_count
    while True:
        lim = limits()
        with _lock:
            if lim.requests_per_day is not None:
                _sync_day(lim)
                if _day_count >= lim.requests_per_day:
                    raise DailyLimitReached(
                        f"All {lim.requests_per_day} model requests for today are spent; "
                        f"the count starts again at midnight in {lim.day_timezone}"
                    )
            wait = _minute_wait(lim, estimate)
            if wait <= 0:
                entry = None
                if lim.requests_per_minute is not None or lim.tokens_per_minute is not None:
                    entry = [time.monotonic(), estimate]
                    _recent_calls.append(entry)
                if lim.requests_per_day is not None:
                    _day_count += 1
                    _save_day_count(_day, _day_count)
                return entry
        log.info("Waiting %.0fs to stay inside the stated per-minute limit", wait)
        time.sleep(wait)


def count_request() -> None:
    """Count one request that does not go through a wrapped client, such as
    Google's model list. It waits and refuses like any other call, so a request
    the vendor may count cannot pass a stated limit unseen."""
    _reserve(0)


def _settle(entry, result) -> None:
    """Replace a call's estimated tokens with the vendor's own count."""
    if entry is None:
        return
    used = _tokens_used(result)
    if used:
        with _lock:
            entry[1] = used


def throttled(call, raw_call=None):
    """Wrap one callable so it stays inside the vendor's limit and waits out a
    refusal for exactly as long as the vendor asked.

    ``raw_call`` is the same request with the HTTP response attached
    (``client.with_raw_response.create``). When given, it is used instead, so
    the budget headers can be read; the parsed object handed back is identical.
    Without it the call still works and simply learns nothing.

    Raises the vendor's own exception once the attempts are spent — losing an
    analysis is bad, but a silent wrong answer is worse, and the caller above
    records a failed run honestly. Anything that is not a rate limit is raised
    at once: a bad argument does not improve on a second attempt, and retrying
    one spends the budget this exists to protect.

    Every attempt counts against the stated limits, because a retry is a
    request the vendor counts too.
    """
    def run(*args, **kwargs):
        estimate = _estimate_tokens(kwargs)
        for attempt in range(1, _ATTEMPTS + 1):
            _wait_for_the_window()
            # Outside the try: a spent day is not a refusal to wait out.
            entry = _reserve(estimate)
            try:
                if raw_call is not None:
                    raw = raw_call(*args, **kwargs)
                    _note_budget(getattr(raw, "headers", None))
                    result = raw.parse()
                else:
                    result = call(*args, **kwargs)
                _settle(entry, result)
                return result
            except Exception as exc:
                if not rate_limited(exc):
                    raise
                wait = retry_after(exc)
                if attempt == _ATTEMPTS:
                    log.warning(
                        "Vendor still refusing on %s after %d attempts — giving up",
                        which_limit(exc), attempt,
                    )
                    raise
                if wait is None:
                    wait = _FALLBACK_WAIT_SECONDS
                log.info(
                    "Vendor refused an LLM call on %s (attempt %d of %d) — "
                    "waiting the %.0fs it asked for",
                    which_limit(exc), attempt, _ATTEMPTS, wait,
                )
                _forget_header_budget()
                time.sleep(wait)

    return run


def _wrap(owner, method_name: str, raw=None) -> None:
    method = getattr(owner, method_name, None)
    if method is None or getattr(method, "_throttled", False):
        return
    wrapped = throttled(method, raw)
    wrapped._throttled = True
    try:
        setattr(owner, method_name, wrapped)
    except Exception:
        # A client that refuses attribute assignment keeps working
        # unthrottled rather than failing the run.
        log.warning("Could not attach the throttle to %s", type(owner).__name__)


def attach(*llms) -> None:
    """Put the throttle in front of every call these clients make.

    ``llm.client.create`` is the one method every OpenAI-shaped LangChain path
    ends at, so wrapping it covers the whole graph — analysts, debate, trader —
    without touching the vendored submodule. Attaching to the client object
    rather than passing something per-call is the same reason the usage
    tracker and the trace recorder attach here: the stages share these two
    objects and none of them accepts an argument from the app.

    **Gemini's client ends at ``client.models.generate_content`` instead**, and
    until 2026-09-13 nothing wrapped it, so a Gemini deployment had no throttle
    at all. Only the synchronous method is wrapped. The graph and the decision
    pass both call ``invoke``, which is the only path this app uses.

    Idempotent: a client already wrapped is left alone, so building a graph
    twice cannot stack two throttles on one method.
    """
    for llm in llms:
        for name in ("client", "async_client"):
            client = getattr(llm, name, None)
            if client is None:
                continue
            raw = getattr(getattr(client, "with_raw_response", None), "create", None)
            _wrap(client, "create", raw)
        models = getattr(getattr(llm, "client", None), "models", None)
        if models is not None:
            _wrap(models, "generate_content")
