import asyncio
import json
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.brain.no_trade_contract import REASON_CODES, build_no_trade_prompt, parse_reason
from src.brain.strategist import (
    TRADE_SYSTEM_PROMPT,
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
    ClaudeStrategist,
    _resolve_prompt_calibration,
)
from src.config.settings import BrainSettings, _build_brain
from src.core.layer_manager import LayerManager
from src.core.strategic_plan import StrategicPlan


def make_strategist(response=None, *, enabled=True, zero_two=False, journal=None):
    brain = BrainSettings(no_trade_contract_enabled=enabled, use_packages=False)
    if journal:
        brain.validation_audit_dir = str(journal)
        brain.validation_experiment_id = "trial-v1"
        brain.validation_run_id = "run-001"
    settings = SimpleNamespace(
        brain=brain, stage2=SimpleNamespace(enable_zero_two_contract=zero_two)
    )
    client = SimpleNamespace(
        send_message=AsyncMock(
            return_value=json.dumps(
                response or {"reason_code": "NO_TRADE_WEAK_EDGE", "new_trades": []}
            )
        )
    )
    strat = ClaudeStrategist(client, {}, settings)
    strat._build_trade_prompt = AsyncMock(return_value="Frozen thin/conflicting market snapshot")
    return strat, client


@pytest.mark.parametrize("control", [TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO])
def test_trial_removes_trade_pressure_preserves_operational_rules(control):
    prompt = build_no_trade_prompt(control)
    for pressure in (
        "2 to 5",
        "3 to 5",
        "DEFAULT TO ACTION",
        "almost always offers",
        "rare, never the default",
        "Aggressive exploitation",
        "ONLY when",
        "prefer the CONFIRMED side at SMALL size",
        "ABSENT one",
    ):
        assert pressure not in prompt
    for rule in (
        "POSITION GATE",
        "[POS]",
        "1.5%",
        "__MIN_RR_RATIO__",
        "size_usd",
        "MARGIN",
        "thesis_invalidation",
        "stop_loss_price",
        "take_profit_price",
        "max_hold_minutes",
        "trailing_activation_pct",
    ):
        assert rule in prompt
    for code in REASON_CODES:
        assert code in prompt


@pytest.mark.parametrize("reason", sorted(REASON_CODES - {"TRADE"}))
@pytest.mark.parametrize("zero_two", [False, True])
@pytest.mark.asyncio
async def test_all_abstentions_are_successful_single_calls(reason, zero_two):
    strat, client = make_strategist({"new_trades": [], "reason_code": reason}, zero_two=zero_two)
    result = await strat.create_trade_plan()
    assert result is not None and result.new_trades == [] and result.reason_code == reason
    assert client.send_message.await_count == 1
    assert "There is no minimum trade count" in client.send_message.call_args.args[1]


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"new_trades": None},
        {"new_trades": [], "reason_code": "TRADE"},
        {"new_trades": [], "reason_code": "NO_TRADE"},
        {"new_trades": ["bad"], "reason_code": "TRADE"},
        {
            "new_trades": [{"symbol": "BTCUSDT", "direction": "Buy"}],
            "reason_code": "NO_TRADE_CONFLICT",
        },
    ],
)
def test_malformed_contract_is_not_abstention(data):
    with pytest.raises(ValueError):
        parse_reason(data)


@pytest.mark.asyncio
async def test_malformed_provider_response_not_retried():
    strat, client = make_strategist()
    client.send_message.return_value = "not JSON"
    assert await strat.create_trade_plan() is None
    assert client.send_message.await_count == 1


@pytest.mark.parametrize("zero_two", [False, True])
@pytest.mark.asyncio
async def test_flag_off_prompt_is_byte_identical_control(zero_two):
    strat, client = make_strategist(enabled=False, zero_two=zero_two)
    strat.settings.brain.surface_briefing_fields = False
    result = await strat.create_trade_plan()
    assert result is not None
    control = TRADE_SYSTEM_PROMPT_ZERO_TWO if zero_two else TRADE_SYSTEM_PROMPT
    expected = _resolve_prompt_calibration(
        control,
        thin_vol_ratio=strat.settings.brain.quality_skip_thin_vol_ratio,
        heavy_attempts=strat.settings.brain.quality_skip_heavy_attempts,
        min_rr_ratio=strat._resolved_min_rr_ratio(),
    )
    assert client.send_message.call_args.args[1] == expected


@pytest.mark.asyncio
async def test_empty_packages_need_no_model_call():
    strat, client = make_strategist()
    strat.settings.brain.use_packages = True
    strat.services["layer_manager"] = SimpleNamespace(get_coin_packages=lambda: {})
    result = await strat.create_trade_plan()
    assert result.new_trades == [] and result.reason_code == "NO_TRADE_WEAK_EDGE"
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_abstention_preserves_urgent_position_management():
    strat, client = make_strategist(
        {
            "new_trades": [],
            "reason_code": "NO_TRADE_CONFLICT",
            "position_actions": {"BTCUSDT": {"action": "close", "reason": "urgent"}},
        }
    )
    strat._has_urgent_concerns = True
    plan = await strat.create_trade_plan()
    assert plan.new_trades == [] and plan.position_actions["BTCUSDT"].action == "close"
    assert '"position_actions"' in client.send_message.call_args.args[1]


@pytest.mark.asyncio
async def test_valid_trade_still_parses():
    trade = {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": 50}
    strat, _ = make_strategist({"new_trades": [trade], "reason_code": "TRADE"})
    plan = await strat.create_trade_plan()
    assert plan.new_trades == [trade] and plan.reason_code == "TRADE"


@pytest.mark.asyncio
async def test_journal_records_inputs_decision_and_freezes_config(tmp_path):
    strat, client = make_strategist(journal=tmp_path)
    plan = await strat.create_trade_plan()
    assert plan.experiment_id == "trial-v1" and plan.run_id == "run-001"
    saved = json.loads(next((tmp_path / "trial-v1/run-001").glob("*.json")).read_text())
    assert saved["decision_id"] == plan.decision_id
    assert saved["prompt"] == client.send_message.call_args.args[0]
    assert saved["system"] == client.send_message.call_args.args[1]
    assert saved["response"] == client.send_message.return_value
    strat.settings.brain.temperature += 0.1
    assert await strat.create_trade_plan() is None
    assert client.send_message.await_count == 1


@pytest.mark.asyncio
async def test_failed_response_recorded_separately(tmp_path):
    strat, client = make_strategist(journal=tmp_path)
    client.send_message.return_value = "malformed"
    assert await strat.create_trade_plan() is None
    saved = json.loads(next((tmp_path / "trial-v1/run-001").glob("*.json")).read_text())
    assert saved["status"] == "failed" and saved["reason_code"] == ""


@pytest.mark.asyncio
async def test_journal_failure_never_emits_trade_plan(tmp_path):
    strat, client = make_strategist(journal=tmp_path)
    strat.settings.brain.validation_experiment_id = "../unsafe"
    assert await strat.create_trade_plan() is None
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_layer_manager_does_not_execute_or_retry_no_trade():
    strat, client = make_strategist()
    lm = LayerManager.__new__(LayerManager)
    lm.services = {"strategist": strat}
    lm.settings = strat.settings
    lm._call_type = "A"
    lm._layer_active = {3: True}
    lm._current_plan = StrategicPlan(new_trades=[{"symbol": "OLD"}])
    lm._plan_history = []
    lm._cycle_times = defaultdict(list)
    lm._maybe_emit_brain_health = Mock()
    lm._send_plan_telegram = Mock()
    lm._execute_trades_background = AsyncMock()
    await lm._run_brain_cycle()
    assert lm._current_plan.new_trades == []
    assert lm._current_plan.reason_code == "NO_TRADE_WEAK_EDGE"
    assert lm._call_type == "B"
    lm._execute_trades_background.assert_not_awaited()
    assert client.send_message.await_count == 1


@pytest.mark.asyncio
async def test_provenance_persists_to_existing_decision_table():
    lake = SimpleNamespace(write_claude_decision=AsyncMock())
    lm = LayerManager.__new__(LayerManager)
    lm.services = {"data_lake": lake}
    plan = StrategicPlan(
        reason_code="NO_TRADE_LOW_LIQUIDITY", experiment_id="p1", run_id="r1", decision_id="d1"
    )
    lm._record_decision_to_data_lake(plan, 10, "call_a")
    await asyncio.sleep(0)
    saved = json.loads(lake.write_claude_decision.call_args.kwargs["full_response"])
    assert saved["reason_code"] == plan.reason_code and saved["experiment_id"] == "p1"


def test_config_default_off_and_explicit_boolean():
    assert BrainSettings().no_trade_contract_enabled is False
    assert _build_brain({"no_trade_contract_enabled": True}).no_trade_contract_enabled is True
    with pytest.raises(ValueError):
        _build_brain({"no_trade_contract_enabled": "false"})


@pytest.mark.asyncio
async def test_legacy_combined_api_cannot_silently_bypass_trial():
    strat, client = make_strategist()
    with pytest.raises(ValueError, match="Call A"):
        await strat.create_strategic_plan()
    client.send_message.assert_not_awaited()
