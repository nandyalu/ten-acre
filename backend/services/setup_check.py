"""What this deployment still needs before it can run.

Everything here was configured through environment variables a person found by
reading documentation, and everything that went wrong said so only in a log
line nobody was watching. A container came up looking healthy, placed no
orders, and the reason was one `docker logs` away.

**This module reports whether something is configured and never what it is
configured to.** No function returns a key, a secret, or an account number,
and nothing that calls it may add one. That is not tidiness — it is the
security design. ``snapshot_export.py`` writes the settings payload to
``settings.json`` and pushes it to Cloudflare Pages, so a field that can carry
a secret is a field that can publish one. A check that never reads the value
cannot leak it, whatever anyone adds to the exporter later.

**Secrets and guards deliberately stay in the environment.** The obvious
version of this puts the Webull keys on a settings page; a credential in
``BotSetting`` is one careless ``_dump()`` from a public website, and a guard
the app can rewrite from a browser is not a guard. This page teaches — it
shows the lines to paste and says to restart — and stores nothing.
"""
import logging
import os
from dataclasses import dataclass

from backend.database import db
from backend.services import agent, analysis, quotes, sandbox_broker

log = logging.getLogger("trading-experiment.setup_check")


@dataclass(frozen=True)
class Requirement:
    """One thing a deployment needs, and how to give it.

    ``fix`` is the literal text to paste. A self-hoster reading "configure
    your Webull credentials" still has to go and find out how; one reading
    ``WEBULL_APP_KEY=…`` does not.
    """

    key: str
    label: str
    ready: bool
    blocking: bool
    detail: str
    # Lines to paste into .env. Environment configuration only — a sentence
    # here renders inside a code block and reads as something to copy.
    fix: str = ""
    # Something to do in the app instead, as a route the page can link to.
    # A path is not a secret, so this keeps the no-values rule intact.
    action_path: str = ""
    action_label: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "ready": self.ready,
            "blocking": self.blocking,
            "detail": self.detail,
            "fix": self.fix,
            "action_path": self.action_path,
            "action_label": self.action_label,
        }


def _configured(*names: str) -> bool:
    """Whether every named environment variable holds something. The value is
    never returned, logged, or compared."""
    return all((os.environ.get(name) or "").strip() for name in names)


def _setting(key: str) -> str | None:
    """A stored setting, or None if the database cannot answer.

    **Defensive on purpose.** A container whose migrations have not run has no
    ``botsetting`` table, and that is exactly the deployment this page exists
    for — a page that 500s while explaining why nothing works is worse than
    the log line it replaces. An unreadable database reads as "nothing
    configured yet", which is both true and the state a first run is in.
    """
    try:
        return db.get_setting(key)
    except Exception:
        log.warning("Setup check could not read setting %r — treating it as unset", key)
        return None


def _webull() -> Requirement:
    ready = _configured("WEBULL_APP_KEY", "WEBULL_APP_SECRET")
    return Requirement(
        key="webull_credentials",
        label="Webull API credentials",
        ready=ready,
        blocking=True,
        detail=(
            "Connected." if ready else
            "Without these the app cannot read prices or place an order. Create an "
            "app key at developer.webull.com and enable the paper environment."
        ),
        fix="" if ready else "WEBULL_APP_KEY=your-key\nWEBULL_APP_SECRET=your-secret",
    )


def _sandbox() -> Requirement:
    ready = quotes.is_sandbox()
    return Requirement(
        key="sandbox",
        label="Simulated trading",
        ready=ready,
        blocking=True,
        detail=(
            "Every order goes to Webull's sandbox." if ready else
            "The agent refuses to place any order without this. It is the guarantee "
            "that no real money is reachable, and it is checked in code before every "
            "single order rather than trusted from this file."
        ),
        fix="" if ready else "WEBULL_SANDBOX=1",
    )


def _account() -> Requirement:
    """Whether the named account exists at the broker, not merely whether the
    variable holds something.

    **Reachability, for the reason ``_llm`` gives**: "the variable is set" and
    "the broker has that account" are different facts, and only the second one
    places an order. This checked the first alone until 2026-09-10, when the
    sandbox's account numbers changed under a running deployment: the page
    reported ready, the agent was switched on, and every pass logged "this
    deployment will not place orders". The one page whose job is to make that
    visible was the page saying it was fine.

    Three states, because "not set" and "set to something that is not there"
    need different answers from a reader.
    """
    named = True
    try:
        sandbox_broker.configured_account()
    except sandbox_broker.NoAccountConfiguredError:
        named = False

    resolved = False
    if named:
        try:
            # The cached value when there is one, so the page costs a Webull
            # call once rather than on every load.
            resolved = bool(sandbox_broker.get_paper_account_id())
        except Exception:
            log.warning("Setup check could not resolve the configured account", exc_info=True)

    if not named:
        detail = (
            "Name the one simulated account this container may trade. Without it no "
            "order is placed at all. Two containers sharing an account would trade "
            "one book, and afterwards nothing could say which of them did what."
        )
    elif not resolved:
        detail = (
            "Named, but the broker did not return it. The account number, the account "
            "class, or the credentials above do not match a simulated account that "
            "exists — so the agent runs and places nothing. Sandbox account numbers "
            "do change; check the list at Webull rather than a number written down "
            "earlier."
        )
    else:
        detail = "Named, and the broker returned it."

    return Requirement(
        key="account",
        label="The account this deployment owns",
        ready=resolved,
        blocking=True,
        detail=detail,
        # A shaped placeholder, not a real account. The number this deployment
        # owns is the one thing on this page a reader must supply themselves,
        # and an example that looks copyable invites pasting it.
        fix="" if resolved else "WEBULL_ACCOUNT_ID=DEL00000000",
    )


def _llm() -> Requirement:
    """Whether the configured LLM endpoint actually answers.

    Reachability rather than configuration, because "the variable is set" and
    "the endpoint replies" are different facts and only the second one lets an
    analysis run. A model list that comes back empty is how this app already
    reports an endpoint it could not reach.

    **Gemini is asked for its model list too (2026-09-13).** Until then the
    list was always empty for it, so this reported "did not answer" and the
    banner said the deployment was not ready, while the agent and every
    analysis ran normally.
    """
    provider = str(analysis.DEFAULT_CONFIG.get("llm_provider") or "").lower() or "ollama"
    try:
        reachable = bool(analysis.list_models())
    except Exception:
        log.warning("Setup check could not reach the LLM endpoint", exc_info=True)
        reachable = False
    if provider == "google":
        return Requirement(
            key="llm",
            label="Language model endpoint (google)",
            ready=reachable,
            blocking=True,
            detail=(
                "Answering." if reachable else
                "Google did not return a model list for this key. Check that the key "
                "is set and that the Gemini API is enabled for its project."
            ),
            fix="" if reachable else "TRADINGAGENTS_LLM_PROVIDER=google\nGOOGLE_API_KEY=your-key",
        )
    return Requirement(
        key="llm",
        label=f"Language model endpoint ({provider})",
        ready=reachable,
        blocking=True,
        detail=(
            "Answering." if reachable else
            "The endpoint did not answer. A local pool needs to be running and "
            "reachable from inside the container; a hosted provider needs its API key."
        ),
        # The names below are the ones the app itself reads. An earlier version
        # printed LLM_PROVIDER and LLM_BACKEND_URL, which are shorthands the
        # compose template happens to map — and it only maps the first of them,
        # so half this advice did nothing at all. A page that exists to stop
        # people guessing at variable names must not guess at them.
        fix="" if reachable else (
            "OLLAMA_BASE_URL=http://host.docker.internal:11435/v1\n"
            "# or, for a hosted provider:\n"
            "TRADINGAGENTS_LLM_PROVIDER=openai_compatible\n"
            "TRADINGAGENTS_LLM_BACKEND_URL=https://api.example.com/v1\n"
            "OPENAI_COMPATIBLE_API_KEY=your-key"
        ),
    )


def _agent_switched_on() -> Requirement:
    """Last, and not a variable. Everything above is configuration a person
    supplies once; this is the switch that starts the experiment, and the
    moment it is flipped is the date the experiment is dated from."""
    # Read through _setting rather than agent.is_enabled(), which touches the
    # database directly and raises on a container whose migrations have not
    # run yet.
    ready = _setting("agent_enabled") == "on"
    return Requirement(
        key="agent_enabled",
        label="The agent",
        ready=ready,
        blocking=True,
        detail=(
            "Running. It decides on its own schedule from here." if ready else
            "Everything is configured. Switching the agent on starts the experiment, "
            "and today becomes its first day."
        ),
        # A link rather than a sentence: this is the one requirement fixed in
        # the app rather than in the environment, and telling somebody to go
        # and find a page is the behaviour this whole feature exists to end.
        action_path="" if ready else "/settings",
        action_label="" if ready else "Open settings",
    )


def _discord() -> Requirement:
    ready = _configured("DISCORD_WEBHOOK_URL")
    return Requirement(
        key="discord",
        label="Discord notifications",
        ready=ready,
        blocking=False,
        detail=(
            "Posting." if ready else
            "Optional. Without it the site is identical — nothing is lost except a "
            "message when the agent acts."
        ),
        fix="" if ready else "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...",
    )


def _fred() -> Requirement:
    ready = _configured("FRED_API_KEY")
    return Requirement(
        key="fred",
        label="Economic data (FRED)",
        ready=ready,
        blocking=False,
        detail=(
            "Connected." if ready else
            "Optional but recommended, and free. Without it the news analyst infers "
            "rates and the yield curve from headlines instead of reading the actual "
            "series, and says so in its own report."
        ),
        fix="" if ready else "FRED_API_KEY=your-key",
    )


def requirements() -> list[Requirement]:
    """Every check, blocking ones first, in the order a person would fix them."""
    return [_webull(), _sandbox(), _account(), _llm(), _agent_switched_on(), _discord(), _fred()]


def status() -> dict:
    """What the first-run page renders. Booleans and names only."""
    checks = requirements()
    blocking = [check for check in checks if check.blocking]
    return {
        "ready": all(check.ready for check in blocking),
        # True only for a deployment that has never been switched on, which is
        # what decides whether the page interrupts or waits to be visited.
        "first_run": not _setting("agent_enabled"),
        "checks": [check.as_dict() for check in checks],
    }
