"""The settings the experiment can change while it runs.

Every value here is a ``BotSetting`` row, so a change takes effect on the next
job without a redeploy. That matters for the record: flipping an environment
variable would change the model between one morning and the next, and the days
either side of the restart would look like one run.

What is *not* here is as deliberate as what is. There is no sync of a real
brokerage account, no sizing knob, and no way to trade. The agent sizes its own
positions and Python only refuses what cannot be executed as stated.
"""
from fastapi import APIRouter, HTTPException

from backend.database import db
from backend.services import (
    agent, agent_book, analysis, broker, experiment, publish, quotes, research, watchdog,
)
from backend.api.schemas import SettingsOut, SettingsPatchIn

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _current_settings() -> SettingsOut:
    alerts = watchdog.load_config()
    return SettingsOut(
        experiment_start=experiment.start_date(),
        horizon=analysis.get_horizon(),
        llm_model=analysis.get_model(),
        llm_model_choices=analysis.model_choices(),
        alert_move_pct=alerts.move_pct,
        alert_stop_pct=alerts.stop_pct,
        alert_volume_mult=alerts.volume_mult,
        alerts_enabled=alerts.enabled,
        agent_enabled=agent.is_enabled(),
        agent_budget=agent_book.get_budget(),
        research_price=research.get_price(),
        agent_min_win_probability=agent.get_conviction()[0],
        agent_min_risk_reward=agent.get_conviction()[1],
        public=publish.is_public(),
    )


@router.get("", response_model=SettingsOut)
def get_settings():
    return _current_settings()


@router.patch("", response_model=SettingsOut)
def update_settings(payload: SettingsPatchIn):
    if payload.horizon is not None:
        try:
            analysis.set_horizon(payload.horizon)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.llm_model is not None:
        try:
            analysis.set_model(payload.llm_model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    for key, value in (
        ("alert_move_pct", payload.alert_move_pct),
        ("alert_stop_pct", payload.alert_stop_pct),
        ("alert_volume_mult", payload.alert_volume_mult),
    ):
        if value is not None:
            if value <= 0:
                raise HTTPException(status_code=400, detail="Thresholds must be positive.")
            db.set_setting(key, str(value))

    if payload.alerts_enabled is not None:
        db.set_setting("alerts_enabled", "on" if payload.alerts_enabled else "off")

    if payload.agent_budget is not None:
        try:
            agent_book.set_budget(payload.agent_budget)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.agent_min_win_probability is not None or payload.agent_min_risk_reward is not None:
        try:
            agent.set_conviction(
                payload.agent_min_win_probability, payload.agent_min_risk_reward
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.agent_enabled is not None:
        # Switching the agent on outside the sandbox would arm something that
        # refuses every order anyway; saying so beats an agent that silently
        # never trades.
        if payload.agent_enabled and not broker.is_paper():
            raise HTTPException(
                status_code=400,
                detail="The broker is not in paper mode — the agent would refuse every order.",
            )
        agent.set_enabled(payload.agent_enabled)

    return _current_settings()
