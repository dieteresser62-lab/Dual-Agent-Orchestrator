"""Current slot occupancy and its provider-to-role projection."""

from agent_roles import AgentRoleName, AgentSlot, role_for_slot


def current_pre_toml_occupancy() -> dict[AgentSlot, str]:
    """Temporary production occupancy until TOML selects slots in Slice 10."""
    return {
        AgentSlot.IMPLEMENTER: "codex",  # allowlist:provider -- role policy file: transitional occupancy
        AgentSlot.REVIEWER: "claude",  # allowlist:provider -- role policy file: transitional occupancy
        AgentSlot.FINAL_REVIEWER: "claude",  # allowlist:provider -- role policy file: transitional occupancy
    }


def provider_roles() -> dict[str, AgentRoleName]:
    """Derive each provider's role from the occupied slots."""
    roles: dict[str, AgentRoleName] = {}
    for slot, provider in current_pre_toml_occupancy().items():
        role = role_for_slot(slot)
        prior = roles.setdefault(provider, role)
        if prior is not role:
            raise ValueError(f"provider {provider!r} occupies conflicting roles")
    return roles


def role_for_provider(provider: str) -> AgentRoleName | None:
    return provider_roles().get(provider)
