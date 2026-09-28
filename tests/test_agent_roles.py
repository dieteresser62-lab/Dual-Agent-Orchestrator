from __future__ import annotations

import pytest

from agent_roles import AgentRoleName, AgentSlot, role_for_slot
from agent_config import current_pre_toml_occupancy


def test_slots_have_provider_independent_roles() -> None:
    assert role_for_slot(AgentSlot.IMPLEMENTER) is AgentRoleName.IMPLEMENTER
    assert role_for_slot(AgentSlot.REVIEWER) is AgentRoleName.REVIEWER
    assert role_for_slot(AgentSlot.FINAL_REVIEWER) is AgentRoleName.REVIEWER
    with pytest.raises(TypeError):
        role_for_slot("reviewer")  # type: ignore[arg-type]


def test_legacy_occupancy_is_confined_to_config_transition() -> None:
    assert current_pre_toml_occupancy() == {
        AgentSlot.IMPLEMENTER: "codex",
        AgentSlot.REVIEWER: "claude",
        AgentSlot.FINAL_REVIEWER: "claude",
    }
