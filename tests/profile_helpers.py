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


def historical_reviewer_state_profile(
    model: str, effort: str, *, slot: str = "reviewer",
) -> AgentProfileBinding:
    """Preserve the qualification bound into pre-restriction golden records."""
    old_certification = {
        "reviewer": "07c602ac6595c18ed1b58d58b3bb56c5ed6173140a685b51276eb15f263e01db",
        "final_reviewer": "51ca820cfe26d54e013fad58d4cbbac025052ce7514294980720a4f0046880ce",
    }[slot]
    return replace(
        scripted_profile_binding(slot),
        model=model, effort=effort,
        capability_sha256="3492500ce735ee5a236aa474bb322c32c02287421bf5ed15f04dfa2f41a2cc3e",
        transport_sha256="2ea6376d95c6e012529277f7907a91d52907a43ee5835155a9e1f15da1a61cc4",
        rights_sha256="d58b1c96b18baa35c24c9daf74da0625eb8d682f48061561fd4d67f71cfcac5c",
        certification_sha256=old_certification,
    )


def historical_reviewer_role_profile(
    model: str, effort: str, *, slot: str = "reviewer",
) -> RoleProfilePayload:
    return RoleProfilePayload(**historical_reviewer_state_profile(model, effort, slot=slot).__dict__)


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
