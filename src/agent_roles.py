"""Provider-independent role and slot names for agent composition."""

from enum import StrEnum


class AgentRoleName(StrEnum):
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"


class AgentSlot(StrEnum):
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    FINAL_REVIEWER = "final_reviewer"


def role_for_slot(slot: AgentSlot) -> AgentRoleName:
    if not isinstance(slot, AgentSlot):
        raise TypeError("agent slot must be an AgentSlot")
    return (
        AgentRoleName.IMPLEMENTER
        if slot is AgentSlot.IMPLEMENTER
        else AgentRoleName.REVIEWER
    )
