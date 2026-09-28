"""Fully bound scripted profiles for tests that do not launch providers."""

from __future__ import annotations

from dataclasses import replace

from artifact_models import RoleProfilePayload, RunProfilePayload
from native_provider_schema import ANTHROPIC_PROVIDER
from workflow_state import AgentProfileBinding, scripted_profile_binding


def _slot(model: str, provider: str | None = None) -> str:
    if provider == ANTHROPIC_PROVIDER or any(part in model.lower() for part in ("review", "opus", "sonnet")):
        return "reviewer"
    return "implementer"


def bound_role_profile(model: str, effort: str, **overrides: object) -> RoleProfilePayload:
    binding = scripted_profile_binding(_slot(model, overrides.get("provider")))
    values = binding.to_dict()
    values.update(model=model, effort=effort, binary_identity=binding.binary_identity)
    values.update(overrides)
    return RoleProfilePayload(**values)


def bound_state_profile(model: str, effort: str, **overrides: object) -> AgentProfileBinding:
    binding = scripted_profile_binding(_slot(model, overrides.get("provider")))
    return AgentProfileBinding(**{**binding.__dict__, "model": model, "effort": effort, **overrides})


def bound_run_profile(*args: object, **kwargs: object) -> RunProfilePayload:
    if "final_reviewer" not in kwargs:
        reviewer = kwargs.get("reviewer", args[1] if len(args) > 1 else None)
        defaults = scripted_profile_binding("final_reviewer")
        identity = defaults.binary_identity
        kwargs["final_reviewer"] = replace(
            reviewer,
            manufacturer=defaults.manufacturer,
            capability_sha256=defaults.capability_sha256,
            transport_sha256=defaults.transport_sha256,
            rights_sha256=defaults.rights_sha256,
            policy_sha256=defaults.policy_sha256,
            certification_sha256=defaults.certification_sha256,
            binary_identity=identity, binary_identity_sha256=identity.digest,
        )
    return RunProfilePayload(*args, **kwargs)
