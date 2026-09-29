"""Current slot occupancy and its provider-to-role projection."""

from pathlib import Path
from collections.abc import Mapping
import tomllib

from agent_roles import AgentRoleName, AgentSlot, role_for_slot


def current_pre_toml_occupancy() -> dict[AgentSlot, str]:
    """Read the shipped TOML defaults for compatibility callers."""
    with (Path(__file__).resolve().parents[1] / "orchestrator.toml").open("rb") as stream:
        shipped = tomllib.load(stream)
    return {
        slot: shipped["agent_profiles"][shipped["roles"][slot.value]]["provider"]
        for slot in AgentSlot
    }


def provider_roles(occupancy: Mapping[AgentSlot, str] | None = None) -> dict[str, AgentRoleName]:
    """Derive each provider's role from the occupied slots."""
    roles: dict[str, AgentRoleName] = {}
    for slot, provider in (current_pre_toml_occupancy() if occupancy is None else occupancy).items():
        role = role_for_slot(slot)
        prior = roles.setdefault(provider, role)
        if prior is not role:
            raise ValueError(f"provider {provider!r} occupies conflicting roles")
    return roles


def registered_providers() -> frozenset[str]:
    """Providers of the capability register; slot certification binds each role pair."""
    from native_provider_schema import load_capability_table

    return frozenset(item["provider"] for item in load_capability_table()["providers"])


def role_for_provider(provider: str) -> AgentRoleName | None:
    return provider_roles().get(provider)
